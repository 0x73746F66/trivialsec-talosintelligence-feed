import models

feeds: list[models.FeedConfig] = [
    models.FeedConfig(
        name="ipreputation",
        url="https://www.talosintelligence.com/documents/ip-blacklist", # https://snort.org/downloads/ip-block-list
        source="talosintelligence.com",
        disabled=False
    ),
]
