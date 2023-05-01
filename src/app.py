import json
from datetime import datetime, timezone
from uuid import uuid5

from lumigo_tracer import lumigo_tracer

import internals
import config
import models
import services.aws


def process(feed: models.FeedConfig) -> list[models.TalosIntelligence]:
    internals.logger.debug("fetch")
    results = []
    if feed.disabled:
        internals.logger.info(f"{feed.name} [magenta]disabled[/magenta]")
        return []
    file_path = internals.download_file(feed.url)
    if not file_path.exists():
        internals.logger.warning(f"Failed to retrieve {feed.name}")
        return []
    contents = file_path.read_text(encoding='utf8')
    if not contents:
        return []
    for line in contents.splitlines():
        if line.startswith('#'):
            continue
        ip_address = line.strip()
        if not ip_address:
            continue
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        data = models.TalosIntelligence(
            address_id=uuid5(internals.TALOS_NAMESPACE, ip_address),
            ip_address=ip_address,
            feed_name=feed.name,
            feed_url=feed.url,
            first_seen=now,
            last_seen=now,
        )
        if not data.exists() and data.save() and services.aws.store_sqs(
            queue_name=f'{internals.APP_ENV.lower()}-early-warning-service',
            message_body=json.dumps({**data.dict(), **{'source': feed.source}}, cls=internals.JSONEncoder),
            deduplicate=False,
        ):
            results.append(data)

    return results


@lumigo_tracer(
    token=services.aws.get_ssm(f'/{internals.APP_ENV}/{internals.APP_NAME}/Lumigo/token', WithDecryption=True),
    should_report=internals.APP_ENV == "Prod",
    skip_collecting_http_body=True,
    verbose=internals.APP_ENV != "Prod"
)
def handler(event, context):
    for feed in config.feeds:
        internals.logger.info(f"{len(process(feed))} queued records -> {feed.name}")
