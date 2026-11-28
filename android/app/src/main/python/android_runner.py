import os


def start_server(
    data_dir: str,
    frontend_dir: str,
    music_dir: str,
    audiobooks_dir: str = "",
    gemini_api_key: str = "",
    port: int = 8000,
) -> None:
    os.environ["DATA_DIR"] = data_dir
    os.environ["FRONTEND_DIR"] = frontend_dir
    os.environ["MUSIC_DIR"] = music_dir
    if audiobooks_dir:
        os.environ["AUDIOBOOKS_DIR"] = audiobooks_dir
    os.environ["ALLOW_MISSING_API_KEY"] = "true"
    if gemini_api_key:
        os.environ["GEMINI_API_KEY"] = gemini_api_key

    import uvicorn
    import main

    uvicorn.run(
        main.app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
