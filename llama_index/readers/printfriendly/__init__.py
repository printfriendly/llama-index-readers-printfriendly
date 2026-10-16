"""LlamaIndex reader for the PrintFriendly API."""

from llama_index.readers.printfriendly._client import PrintFriendlyError
from llama_index.readers.printfriendly.base import PrintFriendlyReader

__all__ = ["PrintFriendlyError", "PrintFriendlyReader"]
