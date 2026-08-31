from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class UntrustedContent:
    id: str
    source: str
    text: str


@dataclass(frozen=True)
class ExternalContentResult:
    content_id: str
    source: str
    visible_content: str
    prompt_layers: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    permissions: tuple[str, ...]
    rejected_instructions: tuple[str, ...]
    external_effects: tuple[object, ...] = ()

    @property
    def safe(self) -> bool:
        return not self.rejected_instructions


class ExternalContentBoundary:
    """Keeps imported content visible while denying it any authority."""

    _instruction_patterns = (
        re.compile(r"(?i)\b(?:system|assistant|developer)\s*:\s*[^.\n]+"),
        re.compile(r"(?i)\bignore\s+(?:all\s+)?(?:previous|prior)\s+(?:rules|instructions)[^.\n]*"),
        re.compile(r"(?i)\b(?:grant|expand|override|bypass)\b[^.\n]*(?:permission|authority|scope|tool)[^.\n]*"),
        re.compile(r"(?i)\b(?:send|delete|publish|execute|run)\b[^.\n]*(?:files?|data|command|tool)[^.\n]*"),
    )

    def inspect(
        self,
        content: UntrustedContent,
        *,
        protected_prompt_layers: tuple[str, ...],
        allowed_tools: tuple[str, ...],
        permissions: tuple[str, ...],
    ) -> ExternalContentResult:
        if not content.source.strip():
            raise ValueError("External content must retain its source.")
        rejected: list[str] = []
        for pattern in self._instruction_patterns:
            rejected.extend(match.group(0).strip() for match in pattern.finditer(content.text))
        return ExternalContentResult(
            content.id,
            content.source,
            content.text,
            protected_prompt_layers + ("external_untrusted_data",),
            allowed_tools,
            permissions,
            tuple(dict.fromkeys(rejected)),
        )
