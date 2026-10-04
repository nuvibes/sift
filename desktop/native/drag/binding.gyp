{
    "targets": [
        {
            "target_name": "drag",
            "include_dirs": ["<!@(node -p \"require('node-addon-api').include\")"],
            "defines": ["NAPI_DISABLE_CPP_EXCEPTIONS"],
            "conditions": [
                [
                    "OS=='win'",
                    {
                        "sources": ["src/drag_win.cc"],
                        "libraries": ["ole32.lib", "shell32.lib", "user32.lib"],
                        "msvs_settings": {"VCCLCompilerTool": {"ExceptionHandling": 1}},
                    },
                ]
            ],
        }
    ]
}
