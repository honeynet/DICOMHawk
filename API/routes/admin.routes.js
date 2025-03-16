const express = require("express");
const router = express.Router();
router.use(express.json());
router.use(express.urlencoded({ extended: true }));
const path = require('path');
const jwtUtils = require("./../authentication/jwtUtils");
const { timeStamp } = require("console");
logger= require("./../logger")
const robotsTxtContent = `
User-agent: MJ12bot
Disallow: /

User-agent: SemrushBot
Disallow: /

User-agent: *
Disallow: /admin/
Disallow: /admin-config/
Disallow: /secure/
Disallow: /ensurance_data/


# Sitemap location (optional but recommended)
Sitemap: https://www.example.com/sitemap.xml
`;

router.get('/robots.txt',(req,res)=>{
  logger.logEvent("Access /robots.txt",req,"")
  res.type('text/plain');
  res.send(robotsTxtContent);
});

const routes = ['/admin', '/admin-config','/secure'];

routes.forEach((route)=>{
    router.get(route, (req,res)=>{
        logger.logEvent("Access "+route,req,route)       

       const adminAccessToken = jwtUtils.generateAdminAccessToken({username: 'admin' });        
       const  adminRefreshToken = jwtUtils.generateAdminRefreshToken({username: 'admin'});
       res.cookie("adminAccessToken", adminAccessToken, {httpOnly:true, secure:false, sameSite:"Strict"});
       res.cookie("adminRefreshToken", adminRefreshToken, {httpOnly:true, secure:false, sameSite:"Strict"});
       res.sendFile((path.join(__dirname, '../static', 'unauthorized.html')));
       
    
    });
});



module.exports = router; 