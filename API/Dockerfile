
FROM node:20-bookworm

WORKDIR /api

COPY package*.json ./

RUN npm install

COPY . .

RUN npm rebuild sqlite3

EXPOSE 3000


CMD ["node", "app.js"]