from abc import ABCMeta, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional

from rotkehlchen.types import ChecksumEvmAddress

if TYPE_CHECKING:
    from rotkehlchen.accounting.structures.base import HistoryBaseEntry
    from rotkehlchen.chain.evm.node_inquirer import EvmNodeInquirer
    from rotkehlchen.user_messages import MessagesAggregator

    from .base import BaseDecoderTools


class DecoderInterface(metaclass=ABCMeta):

    def __init__(
            self,
            evm_inquirer: 'EvmNodeInquirer',  # pylint: disable=unused-argument
            base_tools: 'BaseDecoderTools',  # pylint: disable=unused-argument
            msg_aggregator: 'MessagesAggregator',
    ) -> None:
        """This is the Decoder interface initialization signature

        To have smaller objects and since few decoders use most of the given objects
        we do not save anything here at the moment (except for the msg_aggregator used to send
        possible errors to the user), but instead let it up to the individual
        decoder to choose what to keep.
        """
        self.msg_aggregator = msg_aggregator

    def addresses_to_decoders(self) -> dict[ChecksumEvmAddress, tuple[Any, ...]]:  # pylint: disable=no-self-use  # noqa: E501
        """Subclasses may implement this to return the mappings of addresses to decode functions"""
        return {}

    @abstractmethod
    def counterparties(self) -> list[str]:  # pylint: disable=no-self-use
        """
        Subclasses implement this to specify which counterparty values are introduced by the module
        """
        ...

    def decoding_rules(self) -> list[Callable]:  # pylint: disable=no-self-use
        """
        Subclasses may implement this to add new generic decoding rules to be attempted
        by the decoding process
        """
        return []

    def enricher_rules(self) -> list[Callable]:  # pylint: disable=no-self-use
        """
        Subclasses may implement this to add new generic decoding rules to be attempted
        by the decoding process
        """
        return []

    def post_decoding_rules(self) -> list[tuple[int, Callable]]:
        """
        Subclasses may implement this to add post processing of the decoded events.
        This will run after the normal decoding step and will only process decoded history events.

        This function should return a list of tuples where the first element is the priority of
        a function and the second element is the function to run. The higher the priority number
        the later the function will be run.
        """
        return []

    def notify_user(self, event: 'HistoryBaseEntry', counterparty: str) -> None:
        """
        Notify the user about a problem during the decoding of ethereum transactions. At the
        moment it doesn't take any error type but in the future it could be added if needed.
        Related issue: https://github.com/rotki/rotki/issues/4965
        """
        self.msg_aggregator.add_error(
            f'Could not identify asset {event.asset} decoding ethereum event in {counterparty}. '
            f'Make sure that it has all the required properties (name, symbol and decimals) and '
            f'try to decode the event again {event.event_identifier.hex()}.',
        )


class ReloadableDecoderMixin(metaclass=ABCMeta):

    @abstractmethod
    def reload_data(self) -> Optional[Mapping[ChecksumEvmAddress, tuple[Any, ...]]]:  # pylint: disable=no-self-use  # noqa: E501
        """Subclasses may implement this to be able to reload some of the decoder's properties
        Returns only new mappings of addresses to decode functions
        """
        ...
