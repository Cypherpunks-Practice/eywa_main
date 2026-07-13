from dependency_injector import containers, providers

from ..core.config import get_settings
from ..services.database_backfill_service import DatabaseBackfillService
from ..services.healthcheck_service import HealthcheckService
from ..services.pool_metadata_service import PoolMetadataService
from ..services.pool_registry_service import PoolRegistryService
from ..services.pool_tvl_service import PoolTvlService
from ..services.receipt_collection_service import ReceiptCollectionService
from ..services.reverse_candidate_scoring_service import ReverseCandidateScoringService
from ..services.reverse_search_service import ReverseSearchService
from ..services.rpc_gateway_service import RpcGatewayService
from ..services.scan_orchestrator_service import ScanOrchestratorService
from ..services.swap_parsing_service import SwapParsingService
from ..services.token_metadata_service import TokenMetadataService
from ..services.trade_enrichment_service import TradeEnrichmentService
from ..services.trader_registry_service import TraderRegistryService
from ..services.trading_persistence_service import TradingPersistenceService
from ..services.transaction_discovery_service import TransactionDiscoveryService


class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(modules=["app.main"])

    settings = providers.Singleton(get_settings)

    rpc_gateway_service = providers.Singleton(
        RpcGatewayService,
        settings=settings,
    )
    healthcheck_service = providers.Factory(HealthcheckService)
    pool_registry_service = providers.Factory(PoolRegistryService)
    trader_registry_service = providers.Factory(TraderRegistryService)
    transaction_discovery_service = providers.Factory(
        TransactionDiscoveryService,
        rpc_gateway_service=rpc_gateway_service,
        settings=settings,
    )
    receipt_collection_service = providers.Factory(
        ReceiptCollectionService,
        rpc_gateway_service=rpc_gateway_service,
        settings=settings,
    )
    reverse_search_service = providers.Factory(
        ReverseSearchService,
        rpc_gateway_service=rpc_gateway_service,
        settings=settings,
    )
    swap_parsing_service = providers.Factory(SwapParsingService)
    pool_metadata_service = providers.Factory(
        PoolMetadataService,
        rpc_gateway_service=rpc_gateway_service,
        settings=settings,
    )
    pool_tvl_service = providers.Factory(
        PoolTvlService,
        rpc_gateway_service=rpc_gateway_service,
        settings=settings,
    )
    token_metadata_service = providers.Factory(
        TokenMetadataService,
        rpc_gateway_service=rpc_gateway_service,
        settings=settings,
    )
    trade_enrichment_service = providers.Factory(
        TradeEnrichmentService,
        rpc_gateway_service=rpc_gateway_service,
        token_metadata_service=token_metadata_service,
        settings=settings,
    )
    database_backfill_service = providers.Factory(
        DatabaseBackfillService,
        token_metadata_service=token_metadata_service,
        pool_metadata_service=pool_metadata_service,
        pool_tvl_service=pool_tvl_service,
        trade_enrichment_service=trade_enrichment_service,
    )
    reverse_candidate_scoring_service = providers.Factory(
        ReverseCandidateScoringService,
        receipt_collection_service=receipt_collection_service,
        swap_parsing_service=swap_parsing_service,
        pool_metadata_service=pool_metadata_service,
        trade_enrichment_service=trade_enrichment_service,
        settings=settings,
    )
    trading_persistence_service = providers.Factory(
        TradingPersistenceService,
        settings=settings,
    )
    scan_orchestrator_service = providers.Factory(
        ScanOrchestratorService,
        trader_registry_service=trader_registry_service,
        pool_registry_service=pool_registry_service,
        transaction_discovery_service=transaction_discovery_service,
        receipt_collection_service=receipt_collection_service,
        reverse_search_service=reverse_search_service,
        reverse_candidate_scoring_service=reverse_candidate_scoring_service,
        swap_parsing_service=swap_parsing_service,
        pool_metadata_service=pool_metadata_service,
        pool_tvl_service=pool_tvl_service,
        trade_enrichment_service=trade_enrichment_service,
        trading_persistence_service=trading_persistence_service,
        rpc_gateway_service=rpc_gateway_service,
        settings=settings,
    )
