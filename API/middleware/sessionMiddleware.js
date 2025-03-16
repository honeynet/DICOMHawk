require('dotenv').config();
const session = require('express-session');


const SESSION_SECRET = process.env.SESSION_SECRET;
// console.log("testing if the session secret is printed", SESSION_SECRET);
const sessionMiddleware = (session({
  secret: SESSION_SECRET,
  resave: false,//session that havent changed are not saved
  saveUninitialized: false, //dont save empty sessions
  cookie: {
    //maxAge: 1000*20, //30 minutes
    secure: false, //true if with HTTPS
    httpOnly: true,
    sameSite: 'Strict'
  }
}));

module.exports = sessionMiddleware;
