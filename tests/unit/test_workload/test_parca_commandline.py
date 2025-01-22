from parca import parca_command_line, PARCA_PORT

COMMON_PREFIX = "/parca --config-path=/etc/parca/parca.yaml "

def test_commandline_default():
    assert parca_command_line() == (
        COMMON_PREFIX +
        "--http-address=:7070 "
        "--storage-active-memory=1048576"
    )

def test_commandline_http_address():
    assert parca_command_line(http_address="foo:8080") == (
        COMMON_PREFIX +
        "--http-address=foo:8080 "
        "--storage-active-memory=1048576"
    )

def test_commandline_mem():
    assert parca_command_line(memory_storage_limit=2024) == (
        COMMON_PREFIX +
        "--http-address=:7070 "
        "--storage-active-memory=2122317824"
    )


def test_persistence():
    assert parca_command_line(enable_persistence=True) == (
        COMMON_PREFIX +
        "--http-address=:7070 "
        f"--enable-persistence "
        "--storage-path=/var/lib/parca"
    )

def test_cloud_config():
    store_config = {
        "remote-store-address": "grpc.polarsignals.com:443",
        "remote-store-bearer-token": "deadbeef",
        "remote-store-insecure": "false",
    }
    assert parca_command_line(store_config=store_config) == (
        COMMON_PREFIX +
        f"--http-address=:{PARCA_PORT} "
        f"--storage-active-memory=1048576 "
        f"--store-address=grpc.polarsignals.com:443 "
        f"--bearer-token=deadbeef "
        f"--insecure=false "
        f"--mode=scraper-only"
    )

