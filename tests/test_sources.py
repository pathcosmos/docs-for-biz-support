from src.config.sources import SOURCES


def test_busan_startup_id_uses_numeric_detail_path_tail() -> None:
    detail_url = "https://www.busanstartup.kr/biz_sup/2201?mcode=biz02"

    assert SOURCES["busan_startup"].id_rule(detail_url) == "2201"
