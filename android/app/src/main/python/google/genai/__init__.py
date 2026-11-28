import base64
import json
from types import SimpleNamespace
from typing import Any

import websockets

from . import types

LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


def _to_camel_speech(speech_cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    if not speech_cfg:
        return None
    vc = speech_cfg.get("voice_config") or {}
    pvc = vc.get("prebuilt_voice_config") or {}
    voice_name = pvc.get("voice_name")
    if voice_name:
        return {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}
    return None


def _parse_server_message(raw: str | bytes) -> SimpleNamespace:
    data = json.loads(raw)

    sc_raw = data.get("serverContent")
    server_content = None
    if isinstance(sc_raw, dict):
        mt_raw = sc_raw.get("modelTurn")
        model_turn = None
        if isinstance(mt_raw, dict):
            parts = []
            for p in mt_raw.get("parts") or []:
                inline_raw = p.get("inlineData")
                inline_data = None
                if isinstance(inline_raw, dict) and inline_raw.get("data"):
                    inline_data = SimpleNamespace(
                        mime_type=inline_raw.get("mimeType", "audio/pcm"),
                        data=base64.b64decode(inline_raw["data"]),
                    )
                parts.append(
                    SimpleNamespace(
                        text=p.get("text"),
                        inline_data=inline_data,
                    )
                )
            model_turn = SimpleNamespace(parts=parts)

        server_content = SimpleNamespace(
            interrupted=bool(sc_raw.get("interrupted", False)),
            turn_complete=bool(sc_raw.get("turnComplete", False)),
            model_turn=model_turn,
        )

    tc_raw = data.get("toolCall")
    tool_call = None
    if isinstance(tc_raw, dict):
        fcs = [
            SimpleNamespace(
                id=fc.get("id"),
                name=fc.get("name"),
                args=fc.get("args") or {},
            )
            for fc in (tc_raw.get("functionCalls") or [])
        ]
        tool_call = SimpleNamespace(function_calls=fcs)

    return SimpleNamespace(server_content=server_content, tool_call=tool_call)


class LiveSession:
    def __init__(self, ws):
        self._ws = ws

    async def send_realtime_input(self, audio: dict[str, Any] | None = None):
        if not audio:
            return
        payload = {
            "realtimeInput": {
                "mediaChunks": [
                    {
                        "mimeType": audio.get("mime_type", "audio/pcm;rate=16000"),
                        "data": base64.b64encode(audio["data"]).decode("ascii"),
                    }
                ]
            }
        }
        await self._ws.send(json.dumps(payload))

    async def send_client_content(self, turns=None, turn_complete: bool = True):
        cc: dict[str, Any] = {"turnComplete": bool(turn_complete)}
        if turns is not None:
            turn_list = turns if isinstance(turns, list) else [turns]
            cc["turns"] = [
                t.to_dict() if hasattr(t, "to_dict") else t for t in turn_list
            ]
        await self._ws.send(json.dumps({"clientContent": cc}))

    async def send_tool_response(self, function_responses: list[Any]):
        payload = {
            "toolResponse": {
                "functionResponses": [
                    fr.to_dict() if hasattr(fr, "to_dict") else fr
                    for fr in function_responses
                ]
            }
        }
        await self._ws.send(json.dumps(payload))

    async def receive(self):
        async for raw in self._ws:
            yield _parse_server_message(raw)

    async def close(self):
        await self._ws.close()


class _LiveConnectCtx:
    def __init__(self, api_key: str, model: str, config: dict[str, Any]):
        self._api_key = api_key
        self._model = model
        self._config = config or {}
        self._ws = None

    async def __aenter__(self) -> LiveSession:
        url = f"{LIVE_WS_URL}?key={self._api_key}"
        self._ws = await websockets.connect(url, max_size=None)

        model_name = self._model if self._model.startswith("models/") else f"models/{self._model}"
        gen_cfg: dict[str, Any] = {
            "responseModalities": self._config.get("response_modalities", ["AUDIO"]),
        }
        speech = _to_camel_speech(self._config.get("speech_config"))
        if speech:
            gen_cfg["speechConfig"] = speech

        setup: dict[str, Any] = {
            "model": model_name,
            "generationConfig": gen_cfg,
        }
        sys_inst = self._config.get("system_instruction")
        if sys_inst:
            setup["systemInstruction"] = {"parts": [{"text": sys_inst}]}
        tools = self._config.get("tools")
        if tools:
            setup["tools"] = [
                {"functionDeclarations": t["function_declarations"]}
                if "function_declarations" in t
                else t
                for t in tools
            ]

        await self._ws.send(json.dumps({"setup": setup}))
        # Wait for setupComplete
        await self._ws.recv()
        return LiveSession(self._ws)

    async def __aexit__(self, exc_type, exc, tb):
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


class _LiveNamespace:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def connect(self, model: str, config: dict[str, Any] | None = None):
        return _LiveConnectCtx(self._api_key, model, config or {})


class _AioNamespace:
    def __init__(self, api_key: str):
        self.live = _LiveNamespace(api_key)


class Client:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.aio = _AioNamespace(api_key)


__all__ = ["Client", "types"]
