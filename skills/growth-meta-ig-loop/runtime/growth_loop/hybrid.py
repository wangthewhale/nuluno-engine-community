from .bridge import BrowserBridge


class HybridProvider:
    """Use a supported host browser for IG without exporting its credentials."""
    def __init__(self, graph_provider, store, profile, policy):
        self.graph = graph_provider
        self.browser = BrowserBridge(store, profile, policy)
        self.use_browser = profile.get("execution", {}).get("instagram") == "browser"
        self.ads_browser = profile.get("execution", {}).get("meta_ads") == "browser"

    def account_ready(self, task):
        if (self.use_browser and task["data"]["channel"] == "instagram") or (self.ads_browser and task["data"]["channel"] == "meta_ads"):
            return self.browser.account_ready(task)
        return self.graph.account_ready(task)

    def publish_instagram(self, task, creative):
        return (self.browser if self.use_browser else self.graph).publish_instagram(task, creative)

    def publish_ads(self, task, creative):
        return (self.browser if self.ads_browser else self.graph).publish_ads(task, creative)

    def observe(self, task, publications):
        if (self.use_browser and task["data"]["channel"] == "instagram") or (self.ads_browser and task["data"]["channel"] == "meta_ads"):
            return self.browser.observe(task, publications)
        return self.graph.observe(task, publications)

    def pause_ads(self, task, publication):
        return (self.browser if self.ads_browser else self.graph).pause_ads(task, publication)
