"""Safe server file operations boundary."""
from pathlib import Path

class FileManager:
    def __init__(self, root): self.root=Path(root).resolve()
    def resolve(self, name=""):
        path=(self.root/name).resolve()
        if path!=self.root and self.root not in path.parents: raise ValueError("Path outside server directory")
        return path
