const redisClient= require("./dbConnectors/redisConnector");
const fs = require('fs');
const threat_intelligence= require('./theatIntelligence')
function getClientIp(req) {
    const ip = req.headers['x-forwarded-for'] || req.connection.remoteAddress || req.socket.remoteAddress || '';
    return ip.includes('::ffff:') ? ip.replace('::ffff:', '') : ip;
  }



async function isIpScanned(ip){
  const ipScanned= await redisClient.lRange("scannedIPs", 0, -1)
  const result= JSON.stringify(ipScanned).includes(ip.toString())
  console.log("Ress"+result)
  return result;
 
}

   async function logEvent(event, req, parameter = "N/A") {
    var ip = ""
    var r_port = ""
    rep=parameter
    if (event==="FileUploaded"){
        ip = req.headers['x-requestor-ip'];
        port = req.headers['port']

    }else{
        ip = getClientIp(req)
        r_port=req.connection.remotePort
    }
    let isScanned= await isIpScanned(ip)

    if  ( !isScanned){
      let repu = await threat_intelligence.getReputationData(ip)
      redisClient.rPush("reputation",JSON.stringify(repu))
      redisClient.rPush("scannedIPs",ip)

    }
    
    try {
  
      const jsonObject = {
        ip: ip,
        timestamp: new Date().toISOString().slice(0, 19),
        messevent: event,
        sessionId: process.env.SESSION_SECRET.substring(0, 4) + Date.now().toString().substring(0, 10),
        port: r_port,
        report: rep,
        known_scanner: fs.readFileSync('./blackhole_list.txt', 'utf8').includes('exampleString')
      };
  
      const jsonString = JSON.stringify(jsonObject);
  
      redisClient.rPush('API_logs', jsonString);
      return;
    } catch (error) {
      console.error('Error:', error);
    }
    // console.log("")
  }
  



  module.exports = { logEvent };