# pylint: disable=no-self-argument,arguments-differ
from uuid import UUID, uuid5
from ipaddress import IPv4Address, IPv6Address, IPv4Network, IPv6Network
from abc import ABCMeta, abstractmethod
from typing import Union, Any, Optional
from datetime import datetime, timezone

from pydantic import (
    validator,
    BaseModel,
    AnyHttpUrl,
)

import internals
import services.aws


class DAL(metaclass=ABCMeta):
    @abstractmethod
    def exists(self, **kwargs) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load(self, **kwargs) -> bool:
        raise NotImplementedError

    @abstractmethod
    def save(self, **kwargs) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete(self, **kwargs) -> bool:
        raise NotImplementedError


class TalosIntelligence(BaseModel, DAL):
    address_id: UUID
    ip_address: Union[IPv4Address, IPv6Address, IPv4Network, IPv6Network]
    feed_name: str
    feed_url: AnyHttpUrl
    first_seen: Optional[datetime]
    last_seen: datetime

    class Config:
        validate_assignment = True

    @validator("first_seen")
    def set_first_seen(cls, first_seen: datetime):
        return first_seen.replace(tzinfo=timezone.utc) if first_seen else None

    @validator("last_seen")
    def set_last_seen(cls, last_seen: datetime):
        return last_seen.replace(tzinfo=timezone.utc) if last_seen else None

    def exists(
        self,
        address_id: Union[UUID, None] = None,
        ip_address: Union[str, None] = None,
    ) -> bool:
        return self.load(address_id, ip_address)

    def load(
        self,
        address_id: Union[UUID, None] = None,
        ip_address: Union[str, None] = None,
    ) -> bool:
        if address_id:
            self.address_id = address_id
        if ip_address:
            self.address_id = uuid5(internals.TALOS_NAMESPACE, str(ip_address))
            self.ip_address = str(ip_address)

        response = services.aws.get_dynamodb(table_name=services.aws.Tables.EWS_TALOS, item_key={'address_id': str(self.address_id)})
        if not response:
            internals.logger.warning(f"Missing talos data for address_id: {self.address_id}")
            return False
        super().__init__(**response)
        return True

    def save(self) -> bool:
        return services.aws.put_dynamodb(table_name=services.aws.Tables.EWS_TALOS, item=self.dict())

    def delete(self) -> bool:
        return services.aws.delete_dynamodb(table_name=services.aws.Tables.EWS_TALOS, item_key={'address_id': str(self.address_id)})


class FeedConfig(BaseModel):
    source: str
    name: str
    url: AnyHttpUrl
    disabled: bool
