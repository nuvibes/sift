// Windows file drag-out, in the two shapes Sift needs.
//
// Electron's built-in startDrag hardcodes 'copyLink', which some drop targets reject
// (electron#15361, closed not-planned). This addon drives the OS drag itself via OLE DoDragDrop,
// advertising DROPEFFECT_COPY | DROPEFFECT_MOVE.
//
// Two drags, and the difference is where the bytes are.
//
//   startDrag(path)          The file is on this machine. Advertise CF_HDROP (a path, and nothing
//                            else, which is all that format can carry) and the receiver copies it
//                            itself. Nothing is read, nothing is copied by us, and a 6 GB clip
//                            starts instantly.
//
//   startStreamedDrag(...)   The file is on ANOTHER machine, which is what client mode is. There is
//                            no path to hand over, and OLE's drag loop ends the instant the mouse
//                            button comes up, so a fetch that takes seconds cannot happen inside
//                            one. Advertise a VIRTUAL FILE instead: a descriptor saying what it is
//                            called and how big it is, and a stream the receiver reads AFTER the
//                            drop. That is the same mechanism a mail client uses to drag an
//                            attachment out without ever writing it to disk first.
//
// The stream reads a local file that the JavaScript side is writing at the same time, from the
// network. When it reaches the end of what has arrived it waits for more rather than reporting the
// end of the file, and it tells the two apart by the marker files the writer leaves: no sockets
// between the two halves, no shared memory, nothing to leak.
//
// IDataObjectAsyncCapability is implemented AND switched on (`SetAsyncMode(TRUE)` beside each
// DoDragDrop), so the receiver may read on a thread of its own. Without it a receiver reads on its
// UI thread, inside Drop, inside a modal loop holding a system-wide mouse capture, and the whole
// desktop stops answering the mouse for as long as the read takes.

#include <napi.h>
#include <windows.h>
#include <shlobj.h>
#include <ole2.h>
#include <string>
#include <vector>

// ---- shared -----------------------------------------------------------------------------------

// OLE is initialised once for the life of the process rather than around each drag.
//
// It must NOT be torn down when a drag ends: a streamed drag hands the receiver an object it goes on
// reading after DoDragDrop has returned, and uninitialising the apartment out from under it is a
// crash in somebody else's process. OleInitialize is reference counted and Electron has already
// called it on this thread, so this only ever adds one that is never given back, which is exactly
// the lifetime a long-lived UI thread wants.
static void EnsureOle() {
  static bool started = false;
  if (!started) {
    OleInitialize(nullptr);
    started = true;
  }
}

static std::wstring WideFrom(const Napi::Value& value) {
  std::u16string s = value.As<Napi::String>().Utf16Value();
  return std::wstring(s.begin(), s.end());
}

static bool Exists(const std::wstring& path) {
  return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES;
}

// ---- IDropSource ------------------------------------------------------------------------------

class DropSource : public IDropSource {
  LONG ref_ = 1;
public:
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
    if (riid == IID_IUnknown || riid == IID_IDropSource) {
      *ppv = static_cast<IDropSource*>(this); AddRef(); return S_OK;
    }
    *ppv = nullptr; return E_NOINTERFACE;
  }
  ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&ref_); }
  ULONG STDMETHODCALLTYPE Release() override {
    LONG r = InterlockedDecrement(&ref_); if (r == 0) delete this; return r;
  }
  HRESULT STDMETHODCALLTYPE QueryContinueDrag(BOOL escapePressed, DWORD keyState) override {
    if (escapePressed) return DRAGDROP_S_CANCEL;
    if (!(keyState & (MK_LBUTTON | MK_RBUTTON))) return DRAGDROP_S_DROP;
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE GiveFeedback(DWORD) override {
    return DRAGDROP_S_USEDEFAULTCURSORS;
  }
};

// ---- the stream over a file that is still arriving ---------------------------------------------

// How long to wait between looks when the reader has caught up with the writer.
static const DWORD kPollMs = 25;

// How long the file may STOP GROWING before the transfer is called dead.
//
// Generous, because it is measured against a network that may simply be slow, and the cost of being
// wrong is a half-written file in somebody's chat window. The writer marks a real failure with a
// file of its own, which is answered immediately. This is only for a writer that has gone away
// without saying so, which means the whole application has.
static const DWORD kStallLimitMs = 60000;

class ArrivingFileStream : public IStream {
  LONG ref_ = 1;
  // TWO PATHS FOR ONE FILE, and the second is not belt and braces.
  //
  // The download is written to a partial file and RENAMED into place when it finishes, so that a
  // transfer interrupted half way can never be mistaken for a complete one. A receiver that opens
  // the stream after that rename (a small file on a fast connection, or a receiver that takes its
  // time) would find the partial gone and wait for a file that will never come back. So the
  // finished name is tried too.
  std::wstring path_;
  std::wstring finished_;
  std::wstring done_;
  std::wstring failed_;
  HANDLE file_ = INVALID_HANDLE_VALUE;
  ULONGLONG position_ = 0;
  // The size the server promised, or -1 when it would not say. With it, the end is known before the
  // bytes arrive; without it, only the writer's `.done` marker says so.
  LONGLONG total_ = -1;

public:
  ArrivingFileStream(const std::wstring& path, const std::wstring& finished, LONGLONG total)
      : path_(path),
        finished_(finished),
        done_(path + L".done"),
        failed_(path + L".failed"),
        total_(total) {}

  ~ArrivingFileStream() {
    if (file_ != INVALID_HANDLE_VALUE) CloseHandle(file_);
  }

  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
    if (riid == IID_IUnknown || riid == IID_IStream || riid == IID_ISequentialStream) {
      *ppv = static_cast<IStream*>(this); AddRef(); return S_OK;
    }
    *ppv = nullptr; return E_NOINTERFACE;
  }
  ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&ref_); }
  ULONG STDMETHODCALLTYPE Release() override {
    LONG r = InterlockedDecrement(&ref_); if (r == 0) delete this; return r;
  }

  HRESULT STDMETHODCALLTYPE Read(void* buffer, ULONG wanted, ULONG* got) override {
    if (got) *got = 0;
    if (wanted == 0) return S_OK;

    DWORD waited = 0;
    for (;;) {
      if (!Open()) {
        // The writer has not created it yet. That is the ordinary first moment of a drag, not a
        // failure, unless it has already given up.
        if (Exists(failed_)) return E_FAIL;
        if (waited >= kStallLimitMs) return E_FAIL;
        Sleep(kPollMs);
        waited += kPollMs;
        continue;
      }

      LARGE_INTEGER at;
      at.QuadPart = static_cast<LONGLONG>(position_);
      if (!SetFilePointerEx(file_, at, nullptr, FILE_BEGIN)) return E_FAIL;

      DWORD read = 0;
      if (!ReadFile(file_, buffer, wanted, &read, nullptr)) return E_FAIL;
      if (read > 0) {
        position_ += read;
        if (got) *got = read;
        return S_OK;
      }

      // Nothing there. Either the whole file has been read, or the writer has not caught up.
      if (total_ >= 0 && static_cast<LONGLONG>(position_) >= total_) return S_OK;
      if (Exists(done_)) return S_OK;
      if (Exists(failed_)) return E_FAIL;
      if (waited >= kStallLimitMs) return E_FAIL;
      Sleep(kPollMs);
      waited += kPollMs;
    }
  }

  HRESULT STDMETHODCALLTYPE Write(const void*, ULONG, ULONG*) override {
    return STG_E_ACCESSDENIED;
  }

  HRESULT STDMETHODCALLTYPE Seek(LARGE_INTEGER move, DWORD origin, ULARGE_INTEGER* now) override {
    LONGLONG target = 0;
    switch (origin) {
      case STREAM_SEEK_SET: target = move.QuadPart; break;
      case STREAM_SEEK_CUR: target = static_cast<LONGLONG>(position_) + move.QuadPart; break;
      case STREAM_SEEK_END:
        // Answered from the promised size, never from how much has arrived. A receiver that sized
        // the file by seeking to its end would otherwise be told whatever had turned up so far.
        if (total_ < 0) return E_NOTIMPL;
        target = total_ + move.QuadPart;
        break;
      default: return STG_E_INVALIDFUNCTION;
    }
    if (target < 0) return STG_E_INVALIDFUNCTION;
    position_ = static_cast<ULONGLONG>(target);
    if (now) now->QuadPart = position_;
    return S_OK;
  }

  HRESULT STDMETHODCALLTYPE SetSize(ULARGE_INTEGER) override { return STG_E_ACCESSDENIED; }

  // Implemented rather than refused, because it is how several receivers actually take the bytes:
  // they make a stream of their own and ask this one to pour itself in.
  HRESULT STDMETHODCALLTYPE CopyTo(IStream* into, ULARGE_INTEGER count, ULARGE_INTEGER* readOut,
                                   ULARGE_INTEGER* writtenOut) override {
    if (!into) return STG_E_INVALIDPOINTER;
    std::vector<char> buffer(1 << 16);
    ULONGLONG read = 0, written = 0;
    while (read < count.QuadPart) {
      ULONG wanted = static_cast<ULONG>(
          (count.QuadPart - read) < buffer.size() ? (count.QuadPart - read) : buffer.size());
      ULONG got = 0;
      HRESULT hr = Read(buffer.data(), wanted, &got);
      if (FAILED(hr)) return hr;
      if (got == 0) break;
      read += got;
      ULONG put = 0;
      hr = into->Write(buffer.data(), got, &put);
      if (FAILED(hr)) return hr;
      written += put;
    }
    if (readOut) readOut->QuadPart = read;
    if (writtenOut) writtenOut->QuadPart = written;
    return S_OK;
  }

  HRESULT STDMETHODCALLTYPE Commit(DWORD) override { return S_OK; }
  HRESULT STDMETHODCALLTYPE Revert() override { return S_OK; }
  HRESULT STDMETHODCALLTYPE LockRegion(ULARGE_INTEGER, ULARGE_INTEGER, DWORD) override {
    return STG_E_INVALIDFUNCTION;
  }
  HRESULT STDMETHODCALLTYPE UnlockRegion(ULARGE_INTEGER, ULARGE_INTEGER, DWORD) override {
    return STG_E_INVALIDFUNCTION;
  }

  HRESULT STDMETHODCALLTYPE Stat(STATSTG* out, DWORD flags) override {
    if (!out) return STG_E_INVALIDPOINTER;
    ZeroMemory(out, sizeof(STATSTG));
    out->type = STGTY_STREAM;
    out->cbSize.QuadPart = total_ >= 0 ? static_cast<ULONGLONG>(total_) : 0;
    if (!(flags & STATFLAG_NONAME)) {
      size_t bytes = (path_.size() + 1) * sizeof(wchar_t);
      out->pwcsName = static_cast<LPOLESTR>(CoTaskMemAlloc(bytes));
      if (out->pwcsName) memcpy(out->pwcsName, path_.c_str(), bytes);
    }
    return S_OK;
  }

  HRESULT STDMETHODCALLTYPE Clone(IStream** out) override {
    if (!out) return STG_E_INVALIDPOINTER;
    ArrivingFileStream* copy = new ArrivingFileStream(path_, finished_, total_);
    copy->position_ = position_;
    *out = copy;
    return S_OK;
  }

private:
  bool Open() {
    if (file_ != INVALID_HANDLE_VALUE) return true;
    // Every share flag, because the other half of this is still writing the file. Without
    // FILE_SHARE_WRITE the open fails outright; without FILE_SHARE_DELETE the writer cannot rename
    // it into place when it finishes. An already-open handle follows the file through that rename,
    // which is why only the FIRST open has to consider both names.
    for (const std::wstring& where : {path_, finished_}) {
      if (where.empty()) continue;
      file_ = CreateFileW(where.c_str(), GENERIC_READ,
                          FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                          OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
      if (file_ != INVALID_HANDLE_VALUE) return true;
    }
    return false;
  }
};

// ---- the data object --------------------------------------------------------------------------

// One format this object can hand over.
struct Held {
  FORMATETC format;
  STGMEDIUM medium;
};

// Serves whichever formats it was built with, and remembers anything the receiver sets on it.
//
// The remembering matters: a drop target writes CFSTR_PERFORMEDDROPEFFECT back onto the source's
// data object to say what it did with the file. A SetData that refuses makes a well-behaved target
// think the drag failed.
class DataObject : public IDataObject, public IDataObjectAsyncCapability {
  LONG ref_ = 1;
  std::vector<Held> held_;
  BOOL async_ = FALSE;
  BOOL running_ = FALSE;

public:
  ~DataObject() {
    for (Held& one : held_) ReleaseStgMedium(&one.medium);
  }

  void Add(UINT format, LONG index, DWORD tymed, STGMEDIUM medium) {
    Held one{};
    one.format.cfFormat = static_cast<CLIPFORMAT>(format);
    one.format.ptd = nullptr;
    one.format.dwAspect = DVASPECT_CONTENT;
    one.format.lindex = index;
    one.format.tymed = tymed;
    one.medium = medium;
    held_.push_back(one);
  }

  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
    if (riid == IID_IUnknown || riid == IID_IDataObject) {
      *ppv = static_cast<IDataObject*>(this); AddRef(); return S_OK;
    }
    if (riid == IID_IDataObjectAsyncCapability) {
      *ppv = static_cast<IDataObjectAsyncCapability*>(this); AddRef(); return S_OK;
    }
    *ppv = nullptr; return E_NOINTERFACE;
  }
  ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&ref_); }
  ULONG STDMETHODCALLTYPE Release() override {
    LONG r = InterlockedDecrement(&ref_); if (r == 0) delete this; return r;
  }

  HRESULT STDMETHODCALLTYPE GetData(FORMATETC* wanted, STGMEDIUM* out) override {
    if (!wanted || !out) return E_INVALIDARG;
    Held* found = Find(*wanted);
    if (!found) return DV_E_FORMATETC;
    ZeroMemory(out, sizeof(STGMEDIUM));

    if (found->medium.tymed == TYMED_HGLOBAL) {
      // A copy, because the receiver frees what it is given and this object may be asked again.
      SIZE_T size = GlobalSize(found->medium.hGlobal);
      HGLOBAL copy = GlobalAlloc(GHND, size);
      if (!copy) return E_OUTOFMEMORY;
      void* from = GlobalLock(found->medium.hGlobal);
      void* to = GlobalLock(copy);
      if (from && to) memcpy(to, from, size);
      GlobalUnlock(found->medium.hGlobal);
      GlobalUnlock(copy);
      out->tymed = TYMED_HGLOBAL;
      out->hGlobal = copy;
      return S_OK;
    }

    if (found->medium.tymed == TYMED_ISTREAM) {
      // The same stream, with a reference added. It is read once, by whoever took the drop.
      out->tymed = TYMED_ISTREAM;
      out->pstm = found->medium.pstm;
      if (out->pstm) out->pstm->AddRef();
      return S_OK;
    }

    return DV_E_TYMED;
  }

  HRESULT STDMETHODCALLTYPE GetDataHere(FORMATETC*, STGMEDIUM*) override { return E_NOTIMPL; }

  HRESULT STDMETHODCALLTYPE QueryGetData(FORMATETC* wanted) override {
    return (wanted && Find(*wanted)) ? S_OK : DV_E_FORMATETC;
  }

  HRESULT STDMETHODCALLTYPE GetCanonicalFormatEtc(FORMATETC*, FORMATETC* out) override {
    if (out) out->ptd = nullptr;
    return E_NOTIMPL;
  }

  HRESULT STDMETHODCALLTYPE SetData(FORMATETC* what, STGMEDIUM* medium, BOOL release) override {
    if (!what || !medium) return E_INVALIDARG;
    if (!release) return E_NOTIMPL;  // Would mean copying a medium of unknown shape.
    if (Held* existing = Find(*what)) {
      ReleaseStgMedium(&existing->medium);
      existing->medium = *medium;
      return S_OK;
    }
    Add(what->cfFormat, what->lindex, medium->tymed, *medium);
    return S_OK;
  }

  HRESULT STDMETHODCALLTYPE EnumFormatEtc(DWORD direction, IEnumFORMATETC** out) override {
    if (direction != DATADIR_GET) { if (out) *out = nullptr; return E_NOTIMPL; }
    std::vector<FORMATETC> formats;
    for (Held& one : held_) formats.push_back(one.format);
    return SHCreateStdEnumFmtEtc(static_cast<UINT>(formats.size()), formats.data(), out);
  }

  HRESULT STDMETHODCALLTYPE DAdvise(FORMATETC*, DWORD, IAdviseSink*, DWORD*) override {
    return OLE_E_ADVISENOTSUPPORTED;
  }
  HRESULT STDMETHODCALLTYPE DUnadvise(DWORD) override { return OLE_E_ADVISENOTSUPPORTED; }
  HRESULT STDMETHODCALLTYPE EnumDAdvise(IEnumSTATDATA**) override {
    return OLE_E_ADVISENOTSUPPORTED;
  }

  // ---- IDataObjectAsyncCapability
  //
  // Saying yes to all of this is what lets a receiver take the bytes on a thread of its own. It is
  // the difference between a window that stays alive during a slow transfer and one that appears to
  // have hung. Nothing here needs to DO anything: the stream is safe to read from any thread, so the
  // honest answer to every question is the agreeable one.
  HRESULT STDMETHODCALLTYPE SetAsyncMode(BOOL on) override { async_ = on; return S_OK; }
  HRESULT STDMETHODCALLTYPE GetAsyncMode(BOOL* on) override {
    if (on) *on = async_;
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE StartOperation(IBindCtx*) override { running_ = TRUE; return S_OK; }
  HRESULT STDMETHODCALLTYPE InOperation(BOOL* running) override {
    if (running) *running = running_;
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE EndOperation(HRESULT, IBindCtx*, DWORD) override {
    running_ = FALSE;
    return S_OK;
  }

private:
  Held* Find(const FORMATETC& wanted) {
    for (Held& one : held_) {
      if (one.format.cfFormat == wanted.cfFormat && (wanted.tymed & one.format.tymed) &&
          one.format.lindex == wanted.lindex && one.format.dwAspect == wanted.dwAspect) {
        return &one;
      }
    }
    return nullptr;
  }
};

// ---- building the two payloads ----------------------------------------------------------------

// A CF_HDROP block: a DROPFILES header and a double-null-terminated wide path.
static HGLOBAL BuildHDrop(const std::wstring& path) {
  SIZE_T bytes = sizeof(DROPFILES) + (path.size() + 2) * sizeof(wchar_t);
  HGLOBAL h = GlobalAlloc(GHND, bytes);
  if (!h) return nullptr;
  auto* df = static_cast<DROPFILES*>(GlobalLock(h));
  df->pFiles = sizeof(DROPFILES);
  df->fWide = TRUE;
  auto* dst = reinterpret_cast<wchar_t*>(reinterpret_cast<BYTE*>(df) + sizeof(DROPFILES));
  memcpy(dst, path.c_str(), path.size() * sizeof(wchar_t));
  dst[path.size()] = L'\0';
  dst[path.size() + 1] = L'\0';
  GlobalUnlock(h);
  return h;
}

// A FILEGROUPDESCRIPTORW naming one file: what it is called, and how big it will be.
//
// The size is what makes a receiver's own progress bar mean anything, and what stops Explorer
// asking whether to replace a zero-byte file. When the server would not say how big the file is, the
// size flag is left off rather than a wrong number being given.
static HGLOBAL BuildDescriptor(const std::wstring& name, LONGLONG total) {
  HGLOBAL h = GlobalAlloc(GHND, sizeof(FILEGROUPDESCRIPTORW));
  if (!h) return nullptr;
  auto* group = static_cast<FILEGROUPDESCRIPTORW*>(GlobalLock(h));
  group->cItems = 1;
  FILEDESCRIPTORW& one = group->fgd[0];
  one.dwFlags = FD_ATTRIBUTES | FD_PROGRESSUI;
  one.dwFileAttributes = FILE_ATTRIBUTE_NORMAL;
  if (total >= 0) {
    one.dwFlags |= FD_FILESIZE;
    ULARGE_INTEGER size;
    size.QuadPart = static_cast<ULONGLONG>(total);
    one.nFileSizeLow = size.LowPart;
    one.nFileSizeHigh = size.HighPart;
  }
  wcsncpy_s(one.cFileName, MAX_PATH, name.c_str(), _TRUNCATE);
  GlobalUnlock(h);
  return h;
}

// The two clipboard format NAMES, written out rather than taken from the CFSTR_ macros.
//
// Those macros go through TEXT(), which is narrow unless the whole translation unit is built as
// UNICODE, and this one is not. Using them compiles into a call to the narrow registration
// function with a wide-named format, which is a mismatch the compiler catches only because the two
// functions differ. Written out, there is nothing to get wrong.
static const wchar_t* kFileDescriptorFormat = L"FileGroupDescriptorW";
static const wchar_t* kFileContentsFormat = L"FileContents";

// ---- what JavaScript calls --------------------------------------------------------------------

static Napi::Value StartDrag(const Napi::CallbackInfo& info) {
  Napi::Env env = info.Env();
  if (info.Length() < 1 || !info[0].IsString()) return Napi::Boolean::New(env, false);

  std::wstring path = WideFrom(info[0]);
  EnsureOle();

  HGLOBAL hDrop = BuildHDrop(path);
  if (!hDrop) return Napi::Boolean::New(env, false);

  DataObject* data = new DataObject();
  STGMEDIUM medium{};
  medium.tymed = TYMED_HGLOBAL;
  medium.hGlobal = hDrop;
  data->Add(CF_HDROP, -1, TYMED_HGLOBAL, medium);

  // The same declaration, for the same reason, on the drag that hands over a PATH.
  //
  // Reading CF_HDROP is cheap (it is a string), so this one looked innocent. It is not, when the
  // path is on a NETWORK SHARE: a receiver deciding what it has just been given asks the filesystem
  // about it, and a share that is asleep answers in seconds rather than microseconds. That question
  // is asked inside `Drop`, inside the loop below, with the mouse captured. Async mode moves the
  // whole extraction off the receiver's UI thread, whatever the format, so both drags are answered
  // the same way rather than one of them being right by luck.
  data->SetAsyncMode(TRUE);

  DropSource* source = new DropSource();
  DWORD effect = 0;
  HRESULT hr = DoDragDrop(data, source, DROPEFFECT_COPY | DROPEFFECT_MOVE, &effect);
  // Released and not deleted, exactly as the streamed drag is: with async mode on, a receiver still
  // extracting holds a reference of its own and the object lives until it has finished.
  data->Release();
  source->Release();

  return Napi::Boolean::New(env, hr == DRAGDROP_S_DROP);
}

// startStreamedDrag(partialPath, finishedPath, shownName, totalBytes)
//
// `partialPath` is the file the JavaScript side is writing the download into, and `finishedPath` is
// where it renames it when the download completes. Neither need exist yet: the stream waits. And
// `totalBytes` is -1 when the server would not say how big the file is.
//
// CF_HDROP IS DELIBERATELY NOT OFFERED HERE. A receiver that preferred it would be handed the path
// of a file still being written, would copy whatever had arrived, and would report success. Offering
// only the virtual file means a receiver that cannot take one gets nothing at all, which is the
// right failure: nothing, rather than a truncated video in somebody's chat window.
static Napi::Value StartStreamedDrag(const Napi::CallbackInfo& info) {
  Napi::Env env = info.Env();
  if (info.Length() < 4 || !info[0].IsString() || !info[1].IsString() || !info[2].IsString() ||
      !info[3].IsNumber()) {
    return Napi::Boolean::New(env, false);
  }

  std::wstring path = WideFrom(info[0]);
  std::wstring finished = WideFrom(info[1]);
  std::wstring name = WideFrom(info[2]);
  LONGLONG total = static_cast<LONGLONG>(info[3].As<Napi::Number>().Int64Value());
  EnsureOle();

  HGLOBAL descriptor = BuildDescriptor(name, total);
  if (!descriptor) return Napi::Boolean::New(env, false);

  DataObject* data = new DataObject();

  STGMEDIUM described{};
  described.tymed = TYMED_HGLOBAL;
  described.hGlobal = descriptor;
  data->Add(RegisterClipboardFormatW(kFileDescriptorFormat), -1, TYMED_HGLOBAL, described);

  STGMEDIUM contents{};
  contents.tymed = TYMED_ISTREAM;
  contents.pstm = new ArrivingFileStream(path, finished, total);
  // lindex 0, because FileContents is indexed by position in the group above and there is one file.
  data->Add(RegisterClipboardFormatW(kFileContentsFormat), 0, TYMED_ISTREAM, contents);

  // Async mode is switched on here, by the source, because that is how the contract runs: the
  // source declares async extraction and the target asks `GetAsyncMode` and decides.
  //
  // Without it the target extracts on its own UI thread, inside `IDropTarget::Drop`, inside the
  // `DoDragDrop` below: a modal loop on this thread holding a system-wide mouse capture. For a
  // file coming over a network that read can take a minute (see `kStallLimitMs`), and nothing on
  // the desktop gets mouse input until it ends.
  //
  // The stream is safe to read from any thread, so yes is the true answer.
  data->SetAsyncMode(TRUE);

  DropSource* source = new DropSource();
  DWORD effect = 0;
  HRESULT hr = DoDragDrop(data, source, DROPEFFECT_COPY | DROPEFFECT_MOVE, &effect);
  // Released, not deleted. A receiver doing the extraction asynchronously still holds a reference,
  // and the object stays alive until it has finished reading the stream, which is the entire point
  // of the exercise, and is why OLE is never uninitialised here.
  data->Release();
  source->Release();

  return Napi::Boolean::New(env, hr == DRAGDROP_S_DROP);
}

static Napi::Object Init(Napi::Env env, Napi::Object exports) {
  exports.Set("startDrag", Napi::Function::New(env, StartDrag));
  exports.Set("startStreamedDrag", Napi::Function::New(env, StartStreamedDrag));
  return exports;
}

NODE_API_MODULE(drag, Init)
