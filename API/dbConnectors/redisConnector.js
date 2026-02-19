const redis = require('redis');
host="localhost"
if (process.env.Docker_ENV=="True"){
host="172.29.0.4"
}


var redisClient = redis.createClient({
    socket: {
      host: host, 
      port: 6379  
    }
  });


redisClient.on('error', function(error) {
  console.error(error);
});

redisClient.connect()





 

module.exports = redisClient    