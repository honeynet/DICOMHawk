var getTranslations = function (id) {
    try {
        var xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function () {
            if (xhr.readyState == 4 && xhr.status == 200) {
                try {
                    var json = JSON.parse(xhr.responseText);
                    updateElements(json);
                } catch (err) {
                    console.log(err.message + " in " + xhr.responseText);
                    return;
                }
            }
            return;
        }
        xhr.open("POST", translationUri(id));
        xhr.withCredentials = true;
        xhr.send();
    }
    catch (err) {
        console.log(err.message + " occurred durring error message translation request");
    }
}

var translationUri = function (id) {
    var href = window.location.href;
    var idx = href.indexOf("/", href.indexOf("//") + 2);
    return href.substr(0, idx) + '/synapse/error/TranslatedItems/' + id;
};
