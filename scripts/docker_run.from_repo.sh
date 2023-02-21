image="gordonaspin/icloudds:latest"
container="icloudds"

docker run --detach --name $container -v "~/Documents/iCloud Drive":/data -v ~/.pyicloud:/cookies $image sleep infinite
docker exec -it $container icloudds -d /data --cookie-directory /cookies -u gordon.aspin@gmail.com --log-level info
docker stop $container
docker rm $container