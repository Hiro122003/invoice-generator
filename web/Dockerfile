FROM node:22-alpine

ENV TZ=Asia/Tokyo
WORKDIR /app

# 依存だけ先に入れてレイヤーキャッシュを効かせる
COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
