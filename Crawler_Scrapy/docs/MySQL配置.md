# 服务器 MySQL 数据库使用说明文档

## 1. 当前部署方式

本服务器使用 Docker 部署 MySQL 8.0，并通过 SSH 隧道让本机 Navicat 访问。

| 项目                   | 配置                      |
| ---------------------- | ------------------------- |
| 服务器 IP              | `223.109.239.30`          |
| SSH 端口               | `18316`                   |
| SSH 用户               | `root`                    |
| MySQL 容器名           | `mysql8`                  |
| MySQL 镜像             | `mysql:8.0`               |
| MySQL 服务器内部地址   | `127.0.0.1:3306`          |
| MySQL 数据目录         | `/home/intsig/mysql_data` |
| MySQL root 密码        | `crawler`                 |
| Navicat / 项目用户     | `crawler`                 |
| Navicat / 项目用户密码 | `crawler`                 |
| 字符集                 | `utf8mb4`                 |
| 排序规则               | `utf8mb4_unicode_ci`      |

MySQL 端口绑定方式：

```bash
-p 127.0.0.1:3306:3306
```

含义是：MySQL 只允许服务器本机访问，不直接暴露到公网。  
本机电脑需要通过 SSH 隧道访问数据库。

------

## 2. 启动 MySQL 容器

如果容器还没有创建，可以执行：

```bash
mkdir -p /home/intsig/mysql_data

docker run -d \
  --name mysql8 \
  --restart=always \
  -p 127.0.0.1:3306:3306 \
  -e MYSQL_ROOT_PASSWORD=crawler \
  -e TZ=Asia/Shanghai \
  -v /home/intsig/mysql_data:/var/lib/mysql \
  mysql:8.0 \
  --character-set-server=utf8mb4 \
  --collation-server=utf8mb4_unicode_ci
```

参数说明：

| 参数                                        | 含义                        |
| ------------------------------------------- | --------------------------- |
| `--name mysql8`                             | 容器名称为 `mysql8`         |
| `--restart=always`                          | Docker 启动后自动启动 MySQL |
| `-p 127.0.0.1:3306:3306`                    | 只允许服务器本机访问 MySQL  |
| `MYSQL_ROOT_PASSWORD=crawler`               | root 用户密码               |
| `TZ=Asia/Shanghai`                          | 设置中国时区                |
| `-v /home/intsig/mysql_data:/var/lib/mysql` | 持久化 MySQL 数据           |
| `mysql:8.0`                                 | 使用 MySQL 8.0              |
| `utf8mb4`                                   | 支持中文、特殊符号和 emoji  |

------

## 3. 检查 MySQL 是否正常运行

查看镜像：

```bash
docker images | grep mysql
```

查看容器：

```bash
docker ps -a | grep mysql8
```

查看正在运行的容器：

```bash
docker ps | grep mysql8
```

查看日志：

```bash
docker logs mysql8
```

如果看到：

```text
ready for connections
```

说明 MySQL 已启动成功。

查看端口：

```bash
ss -lntp | grep 3306
```

正常应该看到：

```text
127.0.0.1:3306
```

测试 MySQL：

```bash
docker exec -it mysql8 mysql -uroot -pcrawler -e "SHOW DATABASES;"
```

如果能返回数据库列表，说明 MySQL 正常。

------

## 4. 创建 Navicat / 项目使用账号

为了不修改 root 的认证方式，单独创建 `crawler` 用户，并使用 Navicat 兼容的 `mysql_native_password`。

进入 MySQL：

```bash
docker exec -it mysql8 mysql -uroot -pcrawler
```

执行：

```sql
CREATE USER IF NOT EXISTS 'crawler'@'%' IDENTIFIED WITH mysql_native_password BY 'crawler';

ALTER USER 'crawler'@'%' IDENTIFIED WITH mysql_native_password BY 'crawler';

GRANT ALL PRIVILEGES ON *.* TO 'crawler'@'%' WITH GRANT OPTION;

FLUSH PRIVILEGES;
```

查看用户：

```sql
SELECT user, host, plugin FROM mysql.user;
```

应看到：

```text
crawler    %    mysql_native_password
```

查看权限：

```sql
SHOW GRANTS FOR 'crawler'@'%';
```

退出：

```sql
exit;
```

服务器上测试新用户：

```bash
docker exec -it mysql8 mysql -ucrawler -pcrawler -e "SHOW DATABASES;"
```

------

## 5. 本机通过命令行建立 SSH 隧道

在 Windows CMD / PowerShell 执行：

```bash
ssh -p 18316 -L 3307:127.0.0.1:3306 root@223.109.239.30
```

含义：

| 参数                     | 含义                                        |
| ------------------------ | ------------------------------------------- |
| `-p 18316`               | SSH 端口                                    |
| `-L 3307:127.0.0.1:3306` | 将本机 `3307` 转发到服务器 `127.0.0.1:3306` |
| `root@223.109.239.30`    | 登录服务器                                  |

执行后会进入服务器命令行。这个窗口不要关闭，关闭后隧道会断开。

也可以只建立隧道、不进入交互式命令行：

```bash
ssh -N -p 18316 -L 3307:127.0.0.1:3306 root@223.109.239.30
```

这个窗口会卡住不动，这是正常现象。

------

## 6. 检查本机隧道是否建立成功

在 Windows 另开一个 CMD / PowerShell：

```bat
netstat -ano | findstr 3307
```

如果看到：

```text
127.0.0.1:3307    LISTENING
```

说明隧道成功。

------

## 7. Navicat 连接配置

使用命令行建立 SSH 隧道后，Navicat 不需要勾选 SSH。

### 常规页

| 配置项           | 值           |
| ---------------- | ------------ |
| 连接类型         | MySQL        |
| 连接名           | `jc_crawler` |
| 主机名或 IP 地址 | `127.0.0.1`  |
| 端口             | `3307`       |
| 用户名           | `crawler`    |
| 密码             | `crawler`    |

注意：这里端口是 `3307`，不是 `3306`。  
`3307` 是本机 SSH 隧道端口，会转发到服务器 MySQL 的 `3306`。

### SSH 页

不要勾选 SSH 通道。  
因为 SSH 隧道已经由命令行建立。

------

## 8. 一键启动隧道脚本

可以在 Windows 桌面创建：

```text
start_mysql_tunnel.bat
```

内容：

```bat
@echo off
title MySQL SSH Tunnel
ssh -N -p 18316 -L 3307:127.0.0.1:3306 root@223.109.239.30
pause
```

以后使用流程：

1. 双击 `start_mysql_tunnel.bat`
2. 保持窗口不要关闭
3. 打开 Navicat
4. 连接 `127.0.0.1:3307`

------

## 9. 服务器内部程序连接 MySQL

如果 Python 爬虫、后端程序在服务器上运行，不需要 SSH 隧道，直接连接：

| 配置项   | 值          |
| -------- | ----------- |
| Host     | `127.0.0.1` |
| Port     | `3306`      |
| User     | `crawler`   |
| Password | `crawler`   |

Python 示例：

```python
import pymysql

conn = pymysql.connect(
    host="127.0.0.1",
    port=3306,
    user="crawler",
    password="crawler",
    database="crawler_data",
    charset="utf8mb4"
)

cursor = conn.cursor()
cursor.execute("SHOW TABLES;")
print(cursor.fetchall())

cursor.close()
conn.close()
```

安装驱动：

```bash
pip install pymysql
```

------

## 10. 创建项目数据库

进入 MySQL：

```bash
docker exec -it mysql8 mysql -ucrawler -pcrawler
```

创建数据库：

```sql
CREATE DATABASE crawler_data DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

查看数据库：

```sql
SHOW DATABASES;
```

使用数据库：

```sql
USE crawler_data;
```

创建测试表：

```sql
CREATE TABLE test_table (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    project_name VARCHAR(255) NOT NULL,
    notice_url TEXT,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

插入测试数据：

```sql
INSERT INTO test_table(project_name, notice_url)
VALUES ('测试项目', 'https://example.com/notice');
```

查询：

```sql
SELECT * FROM test_table;
```

------

## 11. 常用 Docker MySQL 管理命令

查看容器：

```bash
docker ps | grep mysql8
```

启动容器：

```bash
docker start mysql8
```

停止容器：

```bash
docker stop mysql8
```

重启容器：

```bash
docker restart mysql8
```

查看日志：

```bash
docker logs -f mysql8
```

进入 MySQL：

```bash
docker exec -it mysql8 mysql -ucrawler -pcrawler
```

使用 root 进入：

```bash
docker exec -it mysql8 mysql -uroot -pcrawler
```

------

## 12. 数据目录和空间查看

MySQL 数据目录：

```bash
/home/intsig/mysql_data
```

查看 MySQL 数据占用空间：

```bash
du -sh /home/intsig/mysql_data
```

查看服务器磁盘空间：

```bash
df -h
```

------

## 13. 备份数据库

备份 `crawler_data`：

```bash
docker exec mysql8 mysqldump -ucrawler -pcrawler crawler_data > /home/intsig/crawler_data_backup.sql
```

查看备份文件：

```bash
ls -lh /home/intsig/crawler_data_backup.sql
```

------

## 14. 恢复数据库

先创建数据库：

```bash
docker exec -it mysql8 mysql -ucrawler -pcrawler -e "CREATE DATABASE crawler_data DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

恢复：

```bash
docker exec -i mysql8 mysql -ucrawler -pcrawler crawler_data < /home/intsig/crawler_data_backup.sql
```

------

## 15. 常见问题

### 15.1 Navicat 连接不上

先检查本机隧道：

```bat
netstat -ano | findstr 3307
```

如果没有 `LISTENING`，重新建立隧道：

```bash
ssh -p 18316 -L 3307:127.0.0.1:3306 root@223.109.239.30
```

### 15.2 Navicat 报 1251 认证错误

错误：

```text
1251 - Client does not support authentication protocol requested by server
```

原因是使用了 MySQL 8 默认的 `caching_sha2_password`。  
解决方式：使用 `crawler` 用户，该用户使用 `mysql_native_password`。

查看：

```sql
SELECT user, host, plugin FROM mysql.user;
```

### 15.3 Navicat 自带 SSH 报错

不使用 Navicat 自带 SSH。  
直接通过命令行建立隧道：

```bash
ssh -p 18316 -L 3307:127.0.0.1:3306 root@223.109.239.30
```

Navicat 连接：

```text
Host: 127.0.0.1
Port: 3307
User: crawler
Password: crawler
```

------

## 16. 推荐使用流程

每次使用 Navicat：

1. Windows 启动 SSH 隧道：

```bash
ssh -N -p 18316 -L 3307:127.0.0.1:3306 root@223.109.239.30
```

2. 保持窗口不关闭。
3. 打开 Navicat，连接：

```text
Host: 127.0.0.1
Port: 3307
User: crawler
Password: crawler
```

4. 使用完成后关闭 Navicat。
5. 关闭 SSH 隧道窗口。

------

## 17. 安全说明

当前 MySQL 没有直接暴露到公网，只监听服务器本机：

```text
127.0.0.1:3306
```

外部电脑不能直接连接服务器的 `3306` 端口，只能通过 SSH 隧道访问。  
这种方式比直接开放 MySQL 端口更安全。