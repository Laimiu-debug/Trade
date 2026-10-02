"""Compatible public entry point for shared pure security identity rules."""
from trade_app.platform.symbols import (
    a_share_board, market_symbol_aliases, market_symbol_key, normalize_a_share_symbol,
    normalize_market_symbol, standard_limit_up_ratio, storage_market_symbol,
)

__all__ = [
    'a_share_board', 'market_symbol_aliases', 'market_symbol_key', 'normalize_a_share_symbol',
    'normalize_market_symbol', 'standard_limit_up_ratio', 'storage_market_symbol',
]
