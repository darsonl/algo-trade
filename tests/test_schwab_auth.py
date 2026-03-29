import os
from schwab_client.auth import get_token_path


def test_get_token_path_is_absolute():
    path = get_token_path()
    assert os.path.isabs(path)


def test_get_token_path_ends_with_json():
    path = get_token_path()
    assert path.endswith(".json")


def test_get_token_path_is_inside_project():
    path = get_token_path()
    # Token should live next to the project root, not in a temp dir
    assert "algo" in path.lower() or "schwab" in path.lower()
