"""
catalog.get_platform_age_ratings() 회귀 테스트.
"""

from conftest import make_chapter_zip


def test_age_ratings_are_aggregated_per_platform(client, library):
    """GIVEN info.xml이 있는 시리즈(연령등급 포함)와 없는 시리즈가 섞여 있을 때"""
    make_chapter_zip(str(library / "naver" / "정보없는웹툰" / "001.zip"))

    kakao_dir = library / "kakao" / "정보있는웹툰"
    make_chapter_zip(str(kakao_dir / "001.zip"))
    (kakao_dir / "info.xml").write_text(
        "<ComicInfo><AgeRating>15세 이용가</AgeRating></ComicInfo>", encoding="utf-8"
    )

    client.post("/api/rescan")

    """WHEN 플랫폼별 연령등급 집계를 조회하면"""
    from app import catalog

    result = catalog.get_platform_age_ratings()

    """THEN 정보 있는 플랫폼은 실제 값이, 정보 없는 플랫폼은 None(정보없음)으로 나온다"""
    assert result["kakao"] == {"15세 이용가"}
    assert result["naver"] == {None}
