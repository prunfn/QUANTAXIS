
---

# clickhouse


docker run -d --name quantaxis-clickhouse -p 8123:8123 -p 9000:9000 -v quantaxis_clickhouse_data:/var/lib/clickhouse -v quantaxis_clickhouse_logs:/var/log/clickhouse-server -e CLICKHOUSE_USER=quantaxis -e CLICKHOUSE_PASSWORD=quantaxis -e CLICKHOUSE_DATABASE=quantaxis -e CLICKHOUSE_DEFAULT_PASSWORD=quantaxis --ulimit nofile=262144:262144 clickhouse:26.3

docker exec -it quantaxis-clickhouse clickhouse-client --user quantaxis --password quantaxis --query "CREATE DATABASE IF NOT EXISTS quantaxis"

# mangoDB

docker run -d --name quantaxis-mongodb -p 27017:27017 -v quantaxis_mongodb_data:/data/db -e MONGO_INITDB_ROOT_USERNAME=quantaxis -e MONGO_INITDB_ROOT_PASSWORD=quantaxis -e MONGO_INITDB_DATABASE=quantaxis mongo:6.0



服务	        镜像版本	        用户名	        密码	        数据库名	        端口	                                连接 URI 示例
MongoDB	    mongo:6.0	    quantaxis	    quantaxis	 quantaxis	    27017	                            mongodb://quantaxis:quantaxis@localhost:27017/quantaxis?authSource=admin
ClickHouse	clickhouse:26.3	quantaxis	    quantaxis	 quantaxis	    8123 (HTTP) / 9000 (Native)	        clickhouse://quantaxis:quantaxis@localhost:9000/quantaxis
