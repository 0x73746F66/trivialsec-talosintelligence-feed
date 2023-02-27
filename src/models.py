import json
from ipaddress import IPv4Address, IPv6Address, IPv4Network, IPv6Network
from abc import ABCMeta, abstractmethod
from typing import Union, Any, Optional
from datetime import datetime, timezone

from pydantic import (
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
    def load(self, **kwargs) -> Union[BaseModel, None]:
        raise NotImplementedError

    @abstractmethod
    def save(self, **kwargs) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete(self, **kwargs) -> bool:
        raise NotImplementedError


class TalosIntelligence(BaseModel):
    ip_address: Union[IPv4Address, IPv6Address, IPv4Network, IPv6Network]
    last_seen: datetime
    category: str


class FeedConfig(BaseModel):
    source: str
    name: str
    url: AnyHttpUrl
    disabled: bool


class FeedStateItem(BaseModel):
    key: str
    data: Optional[Any]
    data_model: Optional[str]
    first_seen: datetime
    current: bool
    entrances: list[datetime]
    exits: list[datetime]


class FeedState(BaseModel):
    source: str
    feed_name: str
    url: Optional[AnyHttpUrl]
    records: Optional[dict[str, FeedStateItem]]
    last_checked: Optional[datetime]

    @property
    def object_key(self):
        return f"{internals.APP_ENV}/feeds/{self.source}/{self.feed_name}/state.json"

    def exit(self, record: str) -> FeedStateItem:
        if item := self.records.get(record):
            item.current = False
            item.exits.append(datetime.now(timezone.utc))
            self.records[record] = item

    def load(self) -> "FeedState":
        raw = services.aws.get_s3(path_key=self.object_key)
        if not raw:
            internals.logger.warning(f"Missing state {self.object_key}")
            return
        try:
            data = json.loads(raw)
        except json.decoder.JSONDecodeError as err:
            internals.logger.debug(err, exc_info=True)
            return
        if not data or not isinstance(data, dict):
            internals.logger.warning(f"Missing state {self.object_key}")
            return
        super().__init__(**data)
        return self

    def save(self) -> bool:
        return services.aws.store_s3(
            self.object_key, json.dumps(self.dict(), default=str)
        )
