# @author lxy
# Java 数据服务镜像：多阶段构建（Maven 构建 + JRE 运行）
# Spring Boot 3.2.5 需要 JDK 17。

# ---- 构建阶段 ----
FROM maven:3.9-eclipse-temurin-17 AS builder
WORKDIR /build
# 先拷贝 pom 预拉依赖，利用层缓存（依赖不变则不重复下载）
COPY pom.xml .
RUN mvn -B -q dependency:go-offline || true
# 拷贝源码并打包（跳过测试，加速构建）
COPY src ./src
RUN mvn -B -q -DskipTests package

# ---- 运行阶段 ----
FROM eclipse-temurin:17-jre
WORKDIR /app
# 从构建阶段拷贝可执行 jar（Spring Boot repackage 后的 fat jar）
COPY --from=builder /build/target/agent-data-service-*.jar app.jar
EXPOSE 8081
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
