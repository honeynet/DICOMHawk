var resizeHandler = function () {
    document.getElementById("center-parent").style.paddingTop = Math.max(window.innerHeight / 4, 70) + 'px';
};
window.addEventListener("resize", resizeHandler);
resizeHandler();