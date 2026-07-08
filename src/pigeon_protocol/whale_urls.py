"""Whale-protected backstage URL paths — mirrors IM SDK Set I."""
from __future__ import annotations

from urllib.parse import urlparse

# Suffix match — official client uses url.endsWith(path)
WHALE_PATH_SUFFIXES: tuple[str, ...] = (
    "/getOrder",
    "/get_order_list",
    "/order/query",
    "/get_product_list",
    "/productdetail",
    "/get_user_footprint",
    "/getuserbyorder",
    "/productdetaillist",
    "/getproductlist",
    "/express_info",
    "/after_sale_get_address_list",
    "/after_sale_get_log_list",
    "/get_skuinfo_list",
    "/id_to_openid",
    "/getCardData",
    "/modifyamount/getdetail",
    "/get_after_sale_list",
    "/get_service_request",
    "/sendCard",
    "/represent_user_biz/aftersale/get",
    "/get_product_properties",
    "/get_product_list_presales",
    "/getTemplateCardData",
    "/xundan_chat_list",
    "/get_goods_comment_info",
    "/get_consulting_products",
    "/get_recommended_products",
)


def is_whale_backstage_url(url: str) -> bool:
    path = urlparse(url).path or ""
    return any(path.endswith(suffix) for suffix in WHALE_PATH_SUFFIXES)
