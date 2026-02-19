const express = require("express");
const router = express.Router();
router.use(express.json());
router.use(express.urlencoded({ extended: true }));
const path = require('path');

const jwtUtils = require("./../authentication/jwtUtils");

//router.post('refreshToken', AuthController.refreshToken);