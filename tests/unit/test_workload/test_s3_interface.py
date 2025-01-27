from s3_interface import S3ConnectionInfo


def test_cacert():
    s3_conn_info = S3ConnectionInfo(
        endpoint="endpoint",
        bucket="bucket",
        access_key="access_key",
        secret_key="secret_key",
        tls_ca_chain=["cert1", "cert2", "cert42"],
    )
    assert s3_conn_info.ca_cert == "cert1\n\ncert2\n\ncert42"
