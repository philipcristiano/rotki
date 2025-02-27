import { type ActionResult } from '@rotki/common/lib/data';
import { axiosSnakeCaseTransformer } from '@/services/axios-tranformers';
import { api } from '@/services/rotkehlchen-api';
import { type PendingTask } from '@/services/types-api';
import {
  handleResponse,
  paramsSerializer,
  validWithParamsSessionAndExternalService
} from '@/services/utils';
import { type CollectionResponse } from '@/types/collection';
import { type EntryWithMeta } from '@/types/history/meta';
import {
  type AssetMovement,
  AssetMovementCollectionResponse,
  type AssetMovementRequestPayload
} from '@/types/history/movements';

export const useAssetMovementsApi = () => {
  const internalAssetMovements = async <T>(
    payload: AssetMovementRequestPayload,
    asyncQuery: boolean
  ): Promise<T> => {
    const response = await api.instance.get<ActionResult<T>>(
      '/asset_movements',
      {
        params: axiosSnakeCaseTransformer({
          asyncQuery,
          ...payload
        }),
        paramsSerializer,
        validateStatus: validWithParamsSessionAndExternalService
      }
    );

    return handleResponse(response);
  };

  const getAssetMovementsTask = async (
    payload: AssetMovementRequestPayload
  ): Promise<PendingTask> => {
    return internalAssetMovements<PendingTask>(payload, true);
  };

  const getAssetMovements = async (
    payload: AssetMovementRequestPayload
  ): Promise<CollectionResponse<EntryWithMeta<AssetMovement>>> => {
    const response = await internalAssetMovements<
      CollectionResponse<EntryWithMeta<AssetMovement>>
    >(payload, false);

    return AssetMovementCollectionResponse.parse(response);
  };

  return {
    getAssetMovements,
    getAssetMovementsTask
  };
};
