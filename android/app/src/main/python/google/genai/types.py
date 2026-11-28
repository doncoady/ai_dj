from dataclasses import dataclass, field
from typing import Any


@dataclass
class Part:
    text: str | None = None
    inline_data: Any = None

    def to_dict(self) -> dict[str, Any]:
        if self.text is not None:
            return {"text": self.text}
        return {}


@dataclass
class Content:
    role: str = "user"
    parts: list[Part] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "parts": [p.to_dict() if hasattr(p, "to_dict") else p for p in self.parts],
        }


@dataclass
class FunctionResponse:
    name: str
    id: str | None = None
    response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "response": self.response}
        if self.id is not None:
            d["id"] = self.id
        return d
