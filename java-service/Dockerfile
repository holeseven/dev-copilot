# @author lxy
FROM maven:3.9-eclipse-temurin-17 AS builder

WORKDIR /build
COPY pom.xml .
RUN mvn -B -q dependency:go-offline || true
COPY src ./src
RUN mvn -B -q -DskipTests package

FROM eclipse-temurin:17-jre

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 devcopilot \
    && useradd --system --uid 10001 --gid devcopilot --home-dir /app devcopilot \
    && mkdir -p /app \
    && chown devcopilot:devcopilot /app

WORKDIR /app
COPY --from=builder --chown=devcopilot:devcopilot /build/target/agent-data-service-*.jar app.jar

USER devcopilot

EXPOSE 8081
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
