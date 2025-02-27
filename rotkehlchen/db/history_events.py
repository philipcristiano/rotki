import logging
from typing import TYPE_CHECKING, Optional, Sequence

from pysqlcipher3 import dbapi2 as sqlcipher

from rotkehlchen.accounting.structures.base import HistoryBaseEntry
from rotkehlchen.assets.asset import Asset
from rotkehlchen.constants import ZERO
from rotkehlchen.constants.limits import FREE_HISTORY_EVENTS_LIMIT
from rotkehlchen.db.constants import (
    HISTORY_MAPPING_KEY_CHAINID,
    HISTORY_MAPPING_KEY_STATE,
    HISTORY_MAPPING_STATE_CUSTOMIZED,
)
from rotkehlchen.db.filtering import HistoryEventFilterQuery
from rotkehlchen.errors.asset import UnknownAsset
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.fval import FVal
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.serialization.deserialize import deserialize_fval
from rotkehlchen.types import ChainID, EVMTxHash, Timestamp, TimestampMS
from rotkehlchen.utils.misc import ts_ms_to_sec

if TYPE_CHECKING:
    from rotkehlchen.db.dbhandler import DBHandler
    from rotkehlchen.db.drivers.gevent import DBCursor

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

HISTORY_INSERT = """INSERT OR IGNORE INTO history_events(event_identifier, sequence_index,
timestamp, location, location_label, asset, amount, usd_value, notes,
type, subtype, counterparty, extra_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);"""


class DBHistoryEvents():

    def __init__(self, database: 'DBHandler') -> None:
        self.db = database

    def add_history_event(    # pylint: disable=no-self-use
            self,
            write_cursor: 'DBCursor',
            event: HistoryBaseEntry,
            mapping_values: Optional[dict[str, int]] = None,
    ) -> Optional[int]:
        """Insert a single history entry to the DB. Returns its identifier or
        None if it already exists.

        Optionally map it to a specific value used to map attributes
        to some events

        May raise:
        - DeserializationError if the event could not be serialized for the DB
        - sqlcipher.IntegrityError: If the asset of the added history event does not exist in
        the DB. Can only happen if an event with an unresolved asset is passed.
        """
        write_cursor.execute(HISTORY_INSERT, event.serialize_for_db())
        if write_cursor.rowcount == 0:
            return None  # already exists

        identifier = write_cursor.lastrowid
        if mapping_values is not None:
            write_cursor.executemany(
                'INSERT OR IGNORE INTO history_events_mappings(parent_identifier, name, value) '
                'VALUES(?, ?, ?)',
                [(identifier, k, v) for k, v in mapping_values.items()],
            )

        return identifier

    def add_history_events(    # pylint: disable=no-self-use
            self,
            write_cursor: 'DBCursor',
            history: Sequence[HistoryBaseEntry],
            chain_id: Optional[ChainID] = None,
    ) -> None:
        """Insert a list of history events in the database.

        Optionally provide a chain id to associate them with in the history
        events mapping table

        May raise:
        - InputError if the events couldn't be stored in the database
        """
        mapping_values = None
        if chain_id is not None:
            mapping_values = {HISTORY_MAPPING_KEY_CHAINID: chain_id.serialize_for_db()}

        for event in history:
            self.add_history_event(
                write_cursor=write_cursor,
                event=event,
                mapping_values=mapping_values,
            )

    def edit_history_event(self, event: HistoryBaseEntry) -> tuple[bool, str]:
        """Edit a history entry to the DB. Returns the edited entry"""
        with self.db.user_write() as cursor:
            try:
                cursor.execute(
                    'UPDATE history_events SET event_identifier=?, sequence_index=?, timestamp=?, '
                    'location=?, location_label=?, asset=?, amount=?, usd_value=?, notes=?, '
                    'type=?, subtype=?, counterparty=?, extra_data=? WHERE identifier=?',
                    (*event.serialize_for_db(), event.identifier),
                )
            except sqlcipher.IntegrityError:  # pylint: disable=no-member
                msg = (
                    f'Tried to edit event to have event_identifier {event.serialized_event_identifier} '  # noqa: 501
                    f'and sequence_index {event.sequence_index} but it already exists'
                )
                return False, msg

            if cursor.rowcount != 1:
                msg = f'Tried to edit event with id {event.identifier} but could not find it in the DB'  # noqa: E501
                return False, msg

            # Also mark it as customized
            cursor.execute(
                'INSERT OR IGNORE INTO history_events_mappings(parent_identifier, name, value) '
                'VALUES(?, ?, ?)',
                (event.identifier, HISTORY_MAPPING_KEY_STATE, HISTORY_MAPPING_STATE_CUSTOMIZED),
            )

        return True, ''

    def delete_history_events_by_identifier(self, identifiers: list[int]) -> Optional[str]:  # noqa: E501
        """
        Delete the history events with the given identifiers. If deleting an event
        makes it the last event of a transaction hash then do not allow deletion.

        If any identifier is missing the entire call fails and an error message
        is returned. Otherwise None is returned.
        """
        for identifier in identifiers:
            with self.db.conn.read_ctx() as cursor:
                cursor.execute(
                    'SELECT COUNT(*) FROM history_events WHERE event_identifier=('
                    'SELECT event_identifier FROM history_events WHERE identifier=?)',
                    (identifier,),
                )
                if cursor.fetchone()[0] == 1:
                    return (
                        f'Tried to remove history event with id {identifier} '
                        f'which was the last event of a transaction'
                    )

            with self.db.user_write() as write_cursor:
                write_cursor.execute(
                    'DELETE FROM history_events WHERE identifier=?', (identifier,),
                )
                affected_rows = write_cursor.rowcount
            if affected_rows != 1:
                return (
                    f'Tried to remove history event with id {identifier} which does not exist'
                )

        return None

    def delete_events_by_tx_hash(
            self,
            write_cursor: 'DBCursor',
            tx_hashes: list[EVMTxHash],
            chain_id: ChainID,
    ) -> None:
        """Delete all relevant (by event_identifier) history events except those that
        are customized"""
        customized_event_ids = self.get_customized_event_identifiers(cursor=write_cursor, chain_id=chain_id)  # noqa: E501
        length = len(customized_event_ids)
        querystr = 'DELETE FROM history_events WHERE event_identifier=?'
        if length != 0:
            querystr += f' AND identifier NOT IN ({", ".join(["?"] * length)})'
            bindings = [(x, *customized_event_ids) for x in tx_hashes]
        else:
            bindings = [(x,) for x in tx_hashes]
        write_cursor.executemany(querystr, bindings)

    def get_customized_event_identifiers(
            self,
            cursor: 'DBCursor',
            chain_id: Optional[ChainID],
    ) -> list[int]:      # pylint: disable=no-self-use
        """Returns the identifiers of all the events in the database that have been customized

        Optionally filter by chain_id
        """
        if chain_id is None:
            cursor.execute(
                'SELECT parent_identifier FROM history_events_mappings WHERE name=? AND value=?',
                (HISTORY_MAPPING_KEY_STATE, HISTORY_MAPPING_STATE_CUSTOMIZED),
            )
        else:
            cursor.execute(
                'SELECT A.parent_identifier FROM history_events_mappings A JOIN '
                'history_events_mappings B ON A.parent_identifier=B.parent_identifier AND '
                'A.name=? AND A.value=? AND B.name=? AND B.value=?',
                (
                    HISTORY_MAPPING_KEY_STATE, HISTORY_MAPPING_STATE_CUSTOMIZED,
                    HISTORY_MAPPING_KEY_CHAINID, chain_id.serialize_for_db(),
                ),
            )

        return [x[0] for x in cursor]

    def get_history_event_by_identifier(self, identifier: int) -> Optional[HistoryBaseEntry]:
        """Returns the history event with the given identifier"""
        with self.db.conn.read_ctx() as cursor:
            cursor.execute('SELECT * FROM history_events WHERE identifier=?', (identifier,))
            entry = cursor.fetchone()
            if entry is None:
                return None

        try:
            deserialized = HistoryBaseEntry.deserialize_from_db(entry)
        except (DeserializationError, UnknownAsset) as e:
            log.debug(f'Failed to deserialize history event {entry} due to {str(e)}')
            return None

        return deserialized

    def get_history_events(
            self,
            cursor: 'DBCursor',
            filter_query: HistoryEventFilterQuery,
            has_premium: bool,
    ) -> list[HistoryBaseEntry]:
        """
        Get history events using the provided query filter
        """
        query, bindings = filter_query.prepare()

        if has_premium:
            query = 'SELECT * from history_events ' + query
            cursor.execute(query, bindings)
        else:
            query = 'SELECT * FROM (SELECT * from history_events ORDER BY timestamp DESC, sequence_index ASC LIMIT ?) ' + query  # noqa: E501
            cursor.execute(query, [FREE_HISTORY_EVENTS_LIMIT] + bindings)

        output = []
        for entry in cursor:
            try:
                deserialized = HistoryBaseEntry.deserialize_from_db(entry)
            except (DeserializationError, UnknownAsset) as e:
                log.debug(f'Failed to deserialize history event {entry} due to {str(e)}')
                continue

            output.append(deserialized)

        return output

    def get_history_events_and_limit_info(
            self,
            cursor: 'DBCursor',
            filter_query: HistoryEventFilterQuery,
            has_premium: bool,
    ) -> tuple[list[HistoryBaseEntry], int]:
        """Gets all history events for the query from the DB

        Also returns how many are the total found for the filter
        """
        events = self.get_history_events(
            cursor=cursor,
            filter_query=filter_query,
            has_premium=has_premium,
        )
        query, bindings = filter_query.prepare(with_pagination=False)
        query = 'SELECT COUNT(*) from history_events ' + query
        cursor.execute(query, bindings)
        return events, cursor.fetchone()[0]  # count always has value

    def rows_missing_prices_in_base_entries(
            self,
            filter_query: HistoryEventFilterQuery,
    ) -> list[tuple[str, FVal, Asset, Timestamp]]:
        """
        Get missing prices for history base entries based on filter query
        """
        query, bindings = filter_query.prepare()
        query = 'SELECT identifier, amount, asset, timestamp FROM history_events ' + query
        result = []
        cursor = self.db.conn.cursor()
        cursor.execute(query, bindings)
        for identifier, amount_raw, asset_identifier, timestamp in cursor:
            try:
                amount = deserialize_fval(
                    value=amount_raw,
                    name='historic base entry usd_value query',
                    location='query_missing_prices',
                )
                result.append(
                    (
                        identifier,
                        amount,
                        Asset(asset_identifier).check_existence(),
                        ts_ms_to_sec(TimestampMS(timestamp)),
                    ),
                )
            except DeserializationError as e:
                log.error(
                    f'Failed to read value from historic base entry {identifier} '
                    f'with amount. {str(e)}',
                )
            except UnknownAsset as e:
                log.error(
                    f'Failed to read asset from historic base entry {identifier} '
                    f'with asset identifier {asset_identifier}. {str(e)}',
                )
        return result

    def get_entries_assets_history_events(
            self,
            cursor: 'DBCursor',
            query_filter: HistoryEventFilterQuery,
    ) -> list[Asset]:
        """Returns asset from base entry events using the desired filter"""
        query, bindings = query_filter.prepare(with_pagination=False)
        query = 'SELECT DISTINCT asset from history_events ' + query
        assets = []
        cursor.execute(query, bindings)
        for asset_id in cursor:
            try:
                assets.append(Asset(asset_id[0]).check_existence())
            except (UnknownAsset, DeserializationError) as e:
                self.db.msg_aggregator.add_error(
                    f'Found asset {asset_id} in the base history events table and '
                    f'is not in the assets database. {str(e)}',
                )
        return assets

    def get_history_events_count(self, cursor: 'DBCursor', query_filter: HistoryEventFilterQuery) -> int:  # noqa: E501  # pylint: disable=no-self-use
        """Returns how many of certain base entry events are in the database"""
        query, bindings = query_filter.prepare(with_pagination=False)
        query = 'SELECT COUNT(*) from history_events ' + query
        cursor.execute(query, bindings)
        return cursor.fetchone()[0]  # count(*) always returns

    def get_value_stats(      # pylint: disable=no-self-use
            self,
            cursor: 'DBCursor',
            query_filter: HistoryEventFilterQuery,
    ) -> tuple[FVal, list[tuple[Asset, FVal, FVal]]]:
        """Returns the sum of the USD value at the time of acquisition and the amount received
        by asset"""
        usd_value = ZERO
        query_filters, bindings = query_filter.prepare(with_pagination=False, with_order=False)
        try:
            query = 'SELECT SUM(CAST(usd_value AS REAL)) FROM history_events ' + query_filters
            result = cursor.execute(query, bindings).fetchone()[0]  # count(*) always returns
            if result is not None:
                usd_value = deserialize_fval(
                    value=result,
                    name='usd value in history events stats',
                    location='get_value_stats',
                )
        except DeserializationError as e:
            log.error(f'Didnt get correct valid usd_value for history_events query. {str(e)}')

        query = (
            f'SELECT asset, SUM(CAST(amount AS REAL)), SUM(CAST(usd_value AS REAL)) '
            f'FROM history_events {query_filters}'
            f' GROUP BY asset;'
        )
        cursor.execute(query, bindings)
        assets_amounts = []
        for row in cursor:
            try:
                asset = Asset(row[0]).check_existence()
                amount = deserialize_fval(
                    value=row[1],
                    name='total amount in history events stats',
                    location='get_value_stats',
                )
                sum_of_usd_values = deserialize_fval(
                    value=row[2],
                    name='total usd value in history events stats',
                    location='get_value_stats',
                )
                assets_amounts.append((asset, amount, sum_of_usd_values))
            except UnknownAsset as e:
                log.debug(f'Found unknown asset {row[0]} in staking event. {str(e)}')
            except DeserializationError as e:
                log.debug(f'Failed to deserialize amount {row[1]}. {str(e)}')
        return usd_value, assets_amounts
