JUJU_MODEL_NAME="test-storage-tls"
PASSWD="foobar"
SAN="minio-0.minio-endpoints.$JUJU_MODEL_NAME.svc.cluster.local"
CN="foo"

echo "with CN:$CN"
echo "with SAN:$SAN"
echo "with PASSWD:$PASSWD"
echo "with JUJU_MODEL_NAME:$JUJU_MODEL_NAME"

echo "create ca-key"
openssl genrsa 2048 > ca-key.pem

echo "create ca-cert"
openssl req -new -x509 -nodes -days 365000 -key ca-key.pem -out ca-cert.pem -subj "/CN=$CN" -addext "subjectAltName = DNS:$SAN"

echo "view ca-cert"
openssl x509 -noout -text -in ca-cert.pem

echo "create server-key and csr (using $PASSWD as password)"
openssl req -new -subj "/CN=$CN" -addext "subjectAltName = DNS:$SAN" -passout pass:$PASSWD -newkey rsa:2048 -keyout server-key.pem -out server-req.pem

echo "decrypt server-key"
openssl rsa -in server-key.pem -passin pass:$PASSWD -out server-key.unencr.pem

echo "obtain server-cert"
openssl x509 -req -days 365000 -set_serial 0 -in server-req.pem -CA ca-cert.pem -CAkey ca-key.pem -out server-cert.pem

echo "configure minio charm"
juju config minio ssl-key="$(cat server-key.unencr.pem | base64)" ssl-cert="$(cat server-cert.pem | base64)"

echo "configure s3 charm"
juju config s3 tls-ca-chain="$(cat ca-cert.pem | base64)"

echo "done!"