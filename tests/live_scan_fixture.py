from __future__ import annotations

from dataclasses import dataclass

from app.schemas import TraceAddressRole

UNSET = "__fill_me__"


@dataclass(frozen=True)
class LiveScanCase:
    block_number: int | None
    trace_address_role: TraceAddressRole
    shark_address: str
    whale_address: str
    expected_pool_address: str
    expected_shark_tx_hash: str
    expected_whale_tx_hash: str
    expected_token_a_address: str
    expected_token_b_address: str
    expected_token_a_symbol: str
    expected_token_b_symbol: str

    @property
    def is_configured(self) -> bool:
        if self.block_number is None or self.block_number <= 0:
            return False

        required_values = (
            self.shark_address,
            self.whale_address,
            self.expected_pool_address,
            self.expected_shark_tx_hash,
            self.expected_whale_tx_hash,
            self.expected_token_a_address,
            self.expected_token_b_address,
            self.expected_token_a_symbol,
            self.expected_token_b_symbol,
        )
        return all(value and value != UNSET for value in required_values)


LIVE_SCAN_CASE = LiveScanCase(
    block_number=24_078_596,
    trace_address_role=TraceAddressRole.TO,
    shark_address="0x51c72848c68a965f66fa7a88855f9f7784502a7f",
    whale_address="0xfbd4cdb413e45a52e2c8312f670e9ce67e794c37",
    expected_pool_address="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
    expected_shark_tx_hash="0xe3b242600ef44bb0cdab877182d3930c39701f77c5f57f94498f6709a3edc19a",
    expected_whale_tx_hash="0x5abf4964dda2422e126d62b7062542b7c6d4c9790299ce2836d862182d443ee5",
    expected_token_a_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    expected_token_b_address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    expected_token_a_symbol="USDC",
    expected_token_b_symbol="WETH",
)
