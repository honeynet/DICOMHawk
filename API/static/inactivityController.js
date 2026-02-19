// function setToCookie(name, value, minutes){
//     const date = new Date();
//     date.setTime(date.getTime()+(minutes*60*1000));
//     const expiration = "expires = " + date.toUTCString();
//     document.cookie = name + "=" + value +";"+expiration + ";path=/";
// }

// function getCookie(name){
//     const nameEqual = name + "=";
//     const cookiesArray = document.cookie.split(';');
//     for(let i = 0; i<cookiesArray.length; i++){
//         let c = cookiesArray[i];
//         while(c.charAt(0)===' '){
//             c = c.substring(1, c.length);
//         }
//         if (c.indexOf(nameEqual)==0){
//             return c.substring(nameEqual.length, c.length);
//         }
//     }
//     return null;
// }

// let inactivityTimeout;
// let alertTimeout;

// const alertTime = 1*60*1000; //2 minutes in milliseconds
// const logoutTimeAfterAlert = 1*60*1000;

// function resetInactivityTimer(){
//     clearTimeout(inactivityTimeout);
//     clearTimeout(alertTimeout);

//     setToCookie('lastActivity', Date.now(), 30);

//     alertTimeout = setTimeout(()=>{
//         alert("You are going to be logged out in 1 minute if no action is detected.");

//         inactivityTimeout = setTimeout(()=>{logoutUser();}, logoutTimeAfterAlert);}, alertTime);
// }

// async function logoutUser(){
//     try{
//         document.body.innerHTML = '<h1>Logging you out...</h1>';
        
//         const response = await fetch('/logout', {
//             method: 'POST',
//             headers: {
//                 'Content-Type': 'application/json'
//             },
//             body: JSON.stringify({})
//         });

//         const result = await response.json();

//         if(response.ok){
//             console.log("Logout successful, redirecting");
//             window.location.href= '/';
//         }

//         else{
//             console.error('Logout failed: ', result.message)
//             alert(result.message);
//         }


//     }catch(err){
//         alert('An unexpected error occurred. Please try again later.');
//         console.error('Unexpected error: ', err);
//     }
// }


// window.onload = resetInactivityTimer;

// document.onmousemove = resetInactivityTimer;
// document.onclick = resetInactivityTimer;
// document.onscroll = resetInactivityTimer;
// document.onkeydown = resetInactivityTimer;
// document.onkeyup = resetInactivityTimer;

// window.onload = function(){
//     const lastActivity = getCookie('lastActivity');
//     if(lastActivity){
//         const now = Date.now();
//         if(now-lastActivity > alertTime){
//             alert("No activity for 2 minutes. You will be logged out in 1 minute if no action is taken.");
//             inactivityTimeout = setTimeout(()=>{
//                 document.body.classList.add('logged-out');
//                 logoutUser();
//             }, logoutTimeAfterAlert);
//         }
//     }
//     resetInactivityTimer();
// }