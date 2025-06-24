FROM node



COPY package*.json /api/
COPY . /api/

WORKDIR /api/.
RUN npm install &&  npm rebuild sqlite3 && npm install redis
EXPOSE 3702

CMD ["node", "app.js"]