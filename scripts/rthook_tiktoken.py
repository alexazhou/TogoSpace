# PyInstaller runtime hook: pre-register tiktoken plugins
# pkgutil.iter_modules doesn't work on namespace packages in frozen bundles,
# so we explicitly import tiktoken_ext.openai_public and register it.
import tiktoken.registry
import tiktoken_ext.openai_public

if tiktoken.registry.ENCODING_CONSTRUCTORS is None:
    tiktoken.registry.ENCODING_CONSTRUCTORS = {}

tiktoken.registry.ENCODING_CONSTRUCTORS.update(
    tiktoken_ext.openai_public.ENCODING_CONSTRUCTORS
)
