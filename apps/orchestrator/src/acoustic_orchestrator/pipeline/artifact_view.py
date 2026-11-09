"""An explicit read-only projection, never a filesystem patch or write permit."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ArtifactView:
    contents: dict[str, bytes | None] = field(default_factory=dict)
    locations: dict[str, str] = field(default_factory=dict)

    def exists(self, path: Path) -> bool:
        key = path.as_posix()
        if key in self.contents:
            return self.contents[key] is not None
        return self.physical(path).exists()

    def read_text(self, path: Path) -> str:
        key = path.as_posix()
        if key in self.contents:
            data = self.contents[key]
            if data is None:
                raise FileNotFoundError(str(path))
            return data.decode("utf-8")
        return self.physical(path).read_text(encoding="utf-8")

    def physical(self, path: Path) -> Path:
        return Path(self.locations.get(path.as_posix(), path))
