import abc
import logging
from functools import reduce
from operator import mul
from typing import TYPE_CHECKING, NamedTuple, Optional

from eth_utils import to_checksum_address
from web3.types import BlockIdentifier

from rotkehlchen.assets.asset import AssetWithOracles, EvmToken
from rotkehlchen.chain.ethereum.utils import token_normalized_value
from rotkehlchen.chain.evm.constants import ZERO_ADDRESS
from rotkehlchen.chain.evm.contracts import EvmContract
from rotkehlchen.constants.assets import A_DAI, A_ETH, A_USD, A_USDC, A_USDT, A_WETH
from rotkehlchen.constants.misc import ONE, ZERO
from rotkehlchen.constants.resolver import ethaddress_to_identifier
from rotkehlchen.errors.asset import UnknownAsset, WrongAssetType
from rotkehlchen.errors.defi import DefiPoolError
from rotkehlchen.errors.price import PriceQueryUnsupportedAsset
from rotkehlchen.fval import FVal
from rotkehlchen.inquirer import Inquirer
from rotkehlchen.interfaces import CurrentPriceOracleInterface
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.types import ChecksumEvmAddress, EvmTokenKind, Price
from rotkehlchen.utils.mixins.cacheable import CacheableMixIn, cache_response_timewise

if TYPE_CHECKING:
    from rotkehlchen.chain.ethereum.node_inquirer import EthereumInquirer

UNISWAP_FACTORY_DEPLOYED_BLOCK = 12369621
SINGLE_SIDE_USD_POOL_LIMIT = 5000

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


class PoolPrice(NamedTuple):
    price: FVal
    token_0: EvmToken
    token_1: EvmToken

    def swap_tokens(self) -> 'PoolPrice':
        return PoolPrice(
            price=1 / self.price,
            token_0=self.token_1,
            token_1=self.token_0,
        )


class UniswapOracle(CurrentPriceOracleInterface, CacheableMixIn):
    """
    Provides shared logic between Uniswap V2 and Uniswap V3 to use them as price oracles.
    """
    def __init__(self, ethereum_inquirer: 'EthereumInquirer', version: int):
        CacheableMixIn.__init__(self)
        CurrentPriceOracleInterface.__init__(self, oracle_name=f'Uniswap V{version} oracle')
        self.ethereum = ethereum_inquirer
        self.weth = A_WETH.resolve_to_evm_token()
        self.routing_assets = [
            self.weth,
            A_DAI.resolve_to_evm_token(),
            A_USDT.resolve_to_evm_token(),
        ]

    def rate_limited_in_last(
            self,
            seconds: Optional[int] = None,  # pylint: disable=unused-argument
    ) -> bool:
        return False

    @abc.abstractmethod
    def get_pool(
            self,
            token_0: EvmToken,
            token_1: EvmToken,
    ) -> list[str]:
        """Given two tokens returns a list of pools where they can be swapped"""
        ...

    @abc.abstractmethod
    def get_pool_price(
            self,
            pool_addr: ChecksumEvmAddress,
            block_identifier: BlockIdentifier = 'latest',
    ) -> PoolPrice:
        """Returns the price for the tokens in the given pool and the token0 and
        token1 of the pool.
        May raise:
        - DefiPoolError
        """
        ...

    def _find_pool_for(
            self,
            asset: EvmToken,
            link_asset: EvmToken,
            path: list[str],
    ) -> bool:
        pools = self.get_pool(asset, link_asset)
        for pool in pools:
            if pool != ZERO_ADDRESS:
                path.append(pool)
                return True

        return False

    def find_route(self, from_asset: EvmToken, to_asset: EvmToken) -> list[str]:
        """
        Calculate the path needed to go from from_asset to to_asset and return a
        list of the pools needed to jump through to do that.
        """
        output: list[str] = []
        # If any of the assets is in the glue assets let's see if we find any path
        # (avoids iterating the list of glue assets)
        if any(x in self.routing_assets for x in (to_asset, from_asset)):
            output = []
            found_path = self._find_pool_for(
                asset=from_asset,
                link_asset=to_asset,
                path=output,
            )
            if found_path:
                return output

        if from_asset == to_asset:
            return []

        # Try to find one asset that can be used between from_asset and to_asset
        # from_asset < first link > glue asset < second link > to_asset
        link_asset = None
        found_first_link, found_second_link = False, False
        for asset in self.routing_assets:
            if asset != from_asset:
                found_first_link = self._find_pool_for(
                    asset=from_asset,
                    link_asset=asset,
                    path=output,
                )
                if found_first_link:
                    link_asset = asset
                    found_second_link = self._find_pool_for(
                        asset=to_asset,
                        link_asset=link_asset,
                        path=output,
                    )
                    if found_second_link:
                        return output

        if not found_first_link:
            return []

        # if we reach this point it means that we need 2 more jumps
        # from asset <1st link> glue asset A <2nd link> glue asset B <3rd link> to asset
        # find now the part for glue asset B <3rd link> to asset
        second_link_asset = None
        for asset in self.routing_assets:
            if asset != to_asset:
                found_second_link = self._find_pool_for(
                    asset=to_asset,
                    link_asset=asset,
                    path=output,
                )
                if found_second_link:
                    second_link_asset = asset
                    break

        if not found_second_link:
            return []

        # finally find the step of glue asset A <2nd link> glue asset B
        assert second_link_asset is not None
        assert link_asset is not None
        pools = self.get_pool(link_asset, second_link_asset)
        for pool in pools:
            if pool != ZERO_ADDRESS:
                output.insert(1, pool)
                return output

        return []

    def get_price(
            self,
            from_asset: AssetWithOracles,
            to_asset: AssetWithOracles,
            block_identifier: BlockIdentifier,
    ) -> Price:
        """
        Return the price of from_asset to to_asset at the block block_identifier.

        Can raise:
        - DefiPoolError
        - RemoteError
        """
        log.debug(
            f'Searching price for {from_asset} to {to_asset} at '
            f'{block_identifier!r} with {self.name}',
        )

        # Uniswap V2 and V3 use in their contracts WETH instead of ETH
        if from_asset == A_ETH:
            from_asset = self.weth
        if to_asset == A_ETH:
            to_asset = self.weth

        try:
            from_token = from_asset.resolve_to_evm_token()
            to_token = to_asset.resolve_to_evm_token()
        except WrongAssetType as e:
            raise PriceQueryUnsupportedAsset(e.identifier) from e

        if from_token == to_token:
            return Price(ONE)

        if from_token.token_kind != EvmTokenKind.ERC20 or to_token.token_kind != EvmTokenKind.ERC20:  # noqa: E501
            raise PriceQueryUnsupportedAsset(f'Either {from_token} or {to_token} is not an ERC20 token')  # noqa: E501

        route = self.find_route(from_token, to_token)

        if len(route) == 0:
            log.debug(f'Failed to find uniswap price for {from_token} to {to_token}')
            return Price(ZERO)
        log.debug(f'Found price route {route} for {from_token} to {to_token} using {self.name}')

        prices_and_tokens = []
        for step in route:
            log.debug(f'Getting pool price for {step}')
            prices_and_tokens.append(
                self.get_pool_price(
                    pool_addr=to_checksum_address(step),
                    block_identifier=block_identifier,
                ),
            )

        # Looking at which one is token0 and token1 we need to see if we need price or 1/price
        if prices_and_tokens[0].token_0 != from_token:
            prices_and_tokens[0] = prices_and_tokens[0].swap_tokens()

        # For the possible intermediate steps also make sure that we use the correct price
        for pos, item in enumerate(prices_and_tokens[1:-1]):
            if item.token_0 != prices_and_tokens[pos - 1].token_1:
                prices_and_tokens[pos - 1] = prices_and_tokens[pos - 1].swap_tokens()

        # Finally for the tail query the price
        if prices_and_tokens[-1].token_1 != to_token:
            prices_and_tokens[-1] = prices_and_tokens[-1].swap_tokens()

        price = FVal(reduce(mul, [item.price for item in prices_and_tokens], 1))
        return Price(price)

    def query_current_price(
            self,
            from_asset: AssetWithOracles,
            to_asset: AssetWithOracles,
            match_main_currency: bool,
    ) -> tuple[Price, bool]:
        """
        This method gets the current price for two ethereum tokens finding a pool
        or a path of pools in the uniswap protocol. The special case of USD as asset
        is handled using USDC instead of USD since is one of the most used stables
        right now for pools.
        Returns:
        1. The price of from_asset at the current timestamp
        for the current oracle
        2. False value, since it never tries to match main currency
        """
        if to_asset == A_USD:
            to_asset = A_USDC.resolve_to_asset_with_oracles()
        elif from_asset == A_USD:
            from_asset = A_USDC.resolve_to_asset_with_oracles()

        price = self.get_price(
            from_asset=from_asset,
            to_asset=to_asset,
            block_identifier='latest',
        )
        return price, False


class UniswapV3Oracle(UniswapOracle):

    def __init__(self, ethereum_inquirer: 'EthereumInquirer'):
        super().__init__(ethereum_inquirer=ethereum_inquirer, version=3)
        self.uniswap_v3_pool_abi = self.ethereum.contracts.abi('UNISWAP_V3_POOL')
        self.uniswap_v3_factory = self.ethereum.contracts.contract('UNISWAP_V3_FACTORY')

    @cache_response_timewise()
    def get_pool(
            self,
            token_0: EvmToken,
            token_1: EvmToken,
    ) -> list[str]:
        result = self.ethereum.multicall_specific(
            contract=self.uniswap_v3_factory,
            method_name='getPool',
            arguments=[[
                token_0.evm_address,
                token_1.evm_address,
                fee,
            ] for fee in (3000, 500, 10000)],
        )

        # get liquidity for each pool and choose the pool with the highest liquidity
        best_pool, max_liquidity = to_checksum_address(result[0][0]), 0
        for query in result:
            if query[0] == ZERO_ADDRESS:
                continue
            pool_address = to_checksum_address(query[0])
            pool_contract = EvmContract(
                address=pool_address,
                abi=self.uniswap_v3_pool_abi,
                deployed_block=UNISWAP_FACTORY_DEPLOYED_BLOCK,
            )
            pool_liquidity = pool_contract.call(
                node_inquirer=self.ethereum,
                method_name='liquidity',
                arguments=[],
                call_order=None,
            )
            if pool_liquidity > max_liquidity:
                best_pool = pool_address
                max_liquidity = pool_liquidity

        if max_liquidity == 0:
            # if there is no pool with assets don't return any pool
            return []
        return [best_pool]

    def get_pool_price(
            self,
            pool_addr: ChecksumEvmAddress,
            block_identifier: BlockIdentifier = 'latest',
    ) -> PoolPrice:
        """
        Returns the units of token1 that one token0 can buy

        May raise:
        - DefiPoolError
        """
        pool_contract = EvmContract(
            address=pool_addr,
            abi=self.uniswap_v3_pool_abi,
            deployed_block=UNISWAP_FACTORY_DEPLOYED_BLOCK,
        )
        calls = [
            (
                pool_contract.address,
                pool_contract.encode(method_name='slot0'),
            ), (
                pool_contract.address,
                pool_contract.encode(method_name='token0'),
            ), (
                pool_contract.address,
                pool_contract.encode(method_name='token1'),
            ),
        ]
        output = self.ethereum.multicall(
            calls=calls,
            require_success=True,
            block_identifier=block_identifier,
        )
        token_0 = EvmToken(
            ethaddress_to_identifier(to_checksum_address(pool_contract.decode(output[1], 'token0')[0])),  # noqa: E501 pylint:disable=unsubscriptable-object
        )
        token_1 = EvmToken(
            ethaddress_to_identifier(to_checksum_address(pool_contract.decode(output[2], 'token1')[0])),  # noqa: E501 pylint:disable=unsubscriptable-object
        )

        sqrt_price_x96, _, _, _, _, _, _ = pool_contract.decode(output[0], 'slot0')
        if token_0.decimals is None:
            raise DefiPoolError(f'Token {token_0} has None as decimals')
        if token_1.decimals is None:
            raise DefiPoolError(f'Token {token_1} has None as decimals')
        decimals_constant = 10 ** (token_0.decimals - token_1.decimals)

        price = FVal((sqrt_price_x96 * sqrt_price_x96) / 2 ** (192) * decimals_constant)

        if ZERO == price:
            raise DefiPoolError(f'Uniswap pool for {token_0}/{token_1} has price 0')

        return PoolPrice(price=price, token_0=token_0, token_1=token_1)


class UniswapV2Oracle(UniswapOracle):

    def __init__(self, ethereum_inquirer: 'EthereumInquirer'):
        super().__init__(ethereum_inquirer=ethereum_inquirer, version=3)
        self.uniswap_v2_lp_abi = self.ethereum.contracts.abi('UNISWAP_V2_LP')
        self.uniswap_v2_factory = self.ethereum.contracts.contract('UNISWAP_V2_FACTORY')

    @cache_response_timewise()
    def get_pool(
            self,
            token_0: EvmToken,
            token_1: EvmToken,
    ) -> list[str]:
        result = self.uniswap_v2_factory.call(
            node_inquirer=self.ethereum,
            method_name='getPair',
            arguments=[
                token_0.evm_address,
                token_1.evm_address,
            ],
        )
        return [result]

    def get_pool_price(
            self,
            pool_addr: ChecksumEvmAddress,
            block_identifier: BlockIdentifier = 'latest',
    ) -> PoolPrice:
        """
        Returns the units of token1 that one token0 can buy

        May raise:
        - DefiPoolError
        """
        pool_contract = EvmContract(
            address=pool_addr,
            abi=self.uniswap_v2_lp_abi,
            deployed_block=10000835,  # Factory deployment block
        )
        calls = [
            (
                pool_contract.address,
                pool_contract.encode(method_name='getReserves'),
            ), (
                pool_contract.address,
                pool_contract.encode(method_name='token0'),
            ), (
                pool_contract.address,
                pool_contract.encode(method_name='token1'),
            ),
        ]
        output = self.ethereum.multicall(
            calls=calls,
            require_success=True,
            block_identifier=block_identifier,
        )

        token_0_address = pool_contract.decode(output[1], 'token0')[0]  # noqa: E501 pylint:disable=unsubscriptable-object
        token_1_address = pool_contract.decode(output[2], 'token1')[0]  # noqa: E501 pylint:disable=unsubscriptable-object

        try:
            token_0 = EvmToken(
                ethaddress_to_identifier(to_checksum_address(token_0_address)),
            )
        except (UnknownAsset, WrongAssetType) as e:
            raise DefiPoolError(f'Failed to read token from address {token_0_address} as ERC-20 token') from e  # noqa: E501

        try:
            token_1 = EvmToken(
                ethaddress_to_identifier(to_checksum_address(token_1_address)),
            )
        except (UnknownAsset, WrongAssetType) as e:
            raise DefiPoolError(f'Failed to read token from address {token_1_address} as ERC-20 token') from e  # noqa: E501

        if token_0.decimals is None:
            raise DefiPoolError(f'Token {token_0} has None as decimals')
        if token_1.decimals is None:
            raise DefiPoolError(f'Token {token_1} has None as decimals')
        reserve_0, reserve_1, _ = pool_contract.decode(output[0], 'getReserves')
        decimals_constant = 10**(token_0.decimals - token_1.decimals)

        if ZERO in (reserve_0, reserve_1):
            raise DefiPoolError(f'Uniswap pool for {token_0}/{token_1} has asset with no reserves')

        # Ignore pools with too low single side-liquidity. Imperfect approach to avoid spam
        # pylint: disable=unexpected-keyword-arg  # no idea why pylint sees this here
        price_0 = Inquirer().find_usd_price(token_0, skip_onchain=True)
        price_1 = Inquirer().find_usd_price(token_1, skip_onchain=True)
        if price_0 != ZERO and price_0 * token_normalized_value(token_amount=reserve_0, token=token_0) < SINGLE_SIDE_USD_POOL_LIMIT:  # noqa: E501
            raise DefiPoolError(f'Uniswap pool for {token_0}/{token_1} has too low reserves')
        if price_1 != ZERO and price_1 * token_normalized_value(token_amount=reserve_1, token=token_1) < SINGLE_SIDE_USD_POOL_LIMIT:  # noqa: E501
            raise DefiPoolError(f'Uniswap pool for {token_0}/{token_1} has too low reserves')

        price = FVal((reserve_1 / reserve_0) * decimals_constant)
        return PoolPrice(price=price, token_0=token_0, token_1=token_1)
