//ES6 compatible browserwes only
(function () {

	class AutoLogoff {

		constructor() {
			this.#init();
		}

		#init() {
			this.#handleSecureImpersonation();
		};

		#handleSecureImpersonation() {
			if (this.#getCookie('ff.authmode') == 'secureimpersonation') {
				let url = new URL(window.location);
				let logOffUrl = url.protocol + "//" + url.hostname + "/SynapseSignOn/Logout.aspx";
				window.location = logOffUrl;
			}
		};

		#getCookie(cname) {
			let name = cname + "=";
			let decodedCookie = decodeURIComponent(document.cookie);
			let ca = decodedCookie.split(';');
			for (let i = 0; i < ca.length; i++) {
				let c = ca[i];
				while (c.charAt(0) == ' ') {
					c = c.substring(1);
				}
				if (c.indexOf(name) == 0) {
					return c.substring(name.length, c.length);
				}
			}
			return "";
		};
	}

	let autoLogoff = new AutoLogoff();

})();

(function () {

	class LayoutHydration {
		#model

		constructor() {
			this.#model = this.#loadModel();
			this.#init();
		}

		#init() {
			if (!this.#model) {
				return;
			}

			this.#setErrorMessage();
			this.#setLicense();
			this.#setFormAction();
			this.#setFormVisibility();
			this.#setUserName();
			this.#setAdditionalLinks();
			this.#setExternalProviders();
			this.#setClosingHrVisibility();
			this.#setExternalProvidersVisibility();
			this.#setAntiforgery();
			this.#validateForm();
			this.#handlePasswordVisability();
			this.#handleHttpPasswordEncryption();
			this.#handleAutoRedirect();
			this.#handleBounce();
		};

		#loadModel() {
			let el = document.getElementById("modelJson").textContent;
			if (el) {
				let json = Encoder.htmlDecode(el);
				return JSON.parse(json);
			} else {
				return null;
			}
		}

		#setErrorMessage(errMsg) {
			let el = this.#getElByClass("alert alert-danger");
			if (!el) return;
			if (!errMsg && this.#model.errorMessage) {
				el.parentNode.style.display = "block";
				let tmpStr = el.innerHTML;
				tmpStr = tmpStr + this.#model.errorMessage;
				el.innerHTML = tmpStr;
			} else if (errMsg) {
				el.parentNode.style.display = "block";
				el.innerHTML = errMsg;
			} else {
				el.parentNode.style.display = "none";
			}
		}

		#setLicense() {
			let shortDateFormat = 'L';

			let el = document.getElementById("licenseIssuedDateID");
			if (el) {
				let issDate = new Date(el.textContent);
				let localized = issDate.toSynapseLocaleString(shortDateFormat);
				el.textContent = localized;
			}

			let exp = document.getElementById("licenseExpirationDateID");
			if (exp) {
				let expDate = new Date(exp.textContent);
				let localized2 = expDate.toSynapseLocaleString(shortDateFormat);
				exp.textContent = localized2;
			}
		}

		#setFormAction() {
			let el = document.getElementById("form");
			if (!el) return;
			el.action = this.#model.loginUrl;
		};

		#validateForm() {
			let d = document, [inputs, loginbtn] = [
				d.getElementsByClassName("form-control"),
				d.querySelector('#login-btn')];
			loginbtn.disabled = true;

			for (let i = 0; i < inputs.length; i++) {
				inputs[i].addEventListener('input', () => {
					let values = [];
					for (let n = 0; n < inputs.length; n++) {
						values.push(inputs[n].value);
					}
					loginbtn.disabled = values.includes('');
				})
			}

			document.getElementById("form").onkeypress = (e) => {
				var key = e.charCode || e.keyCode || 0;
				if (loginbtn.disabled && key == 13) {
					e.preventDefault();
				}
			}
		}

		#handlePasswordVisability() {
			let pwdInput = document.getElementById('password');
			let passStatus = document.getElementById('pass-status');

			let onEyeClick = (e) => {
				if (pwdInput.type === 'password') {
					pwdInput.type = 'text';
					passStatus.className = 'fa fa-eye-slash';
				}
				else {
					pwdInput.type = 'password';
					passStatus.className = 'fa fa-eye';
				}
			}

			passStatus.addEventListener("click", onEyeClick);
		}

		#handleHttpPasswordEncryption() {
			let rsa, rsaPublicKey;

			let encryptPwd = (e) => {
				rsa.setPublicKey(rsaPublicKey);
				let pwdElement = document.getElementById("password");
				let pwdValue = pwdElement.value;
				pwdElement.value = 'encrypted_password';

				let encryptedPwd = rsa.encrypt(pwdValue);
				let encryptedPwdElement = document.getElementById("encryptedpassword");
				encryptedPwdElement.value = encryptedPwd;
			}

			let rsaPublicKeyElement = document.getElementById("publicKey");

			if (location.protocol === "http:" && rsaPublicKeyElement) {
				rsa = new JSEncrypt();
				rsaPublicKey = rsaPublicKeyElement.getAttribute("value");
				let loginForm = document.getElementById("form");
				loginForm.addEventListener("submit", encryptPwd);
			}
		}

		#handleAutoRedirect() {
			if (this.#model.autoRedirect && this.#model.redirectUrl) {
				if (this.#model.autoRedirectDelay < 0) {
					this.#model.autoRedirectDelay = 0;
				}
				window.setTimeout(() => {
					window.location = this.#model.redirectUrl;
				}, this.#model.autoRedirectDelay * 1000);
			}
		}

		#setAntiforgery() {
			if (this.#model.antiForgery && this.#model.antiForgery.name && this.#model.antiForgery.value) {
				let el = document.getElementById("antiForgery");
				if (!el) return;
				el.value = this.#model.antiForgery.value;
				el.name = this.#model.antiForgery.name;
			}
		}

		#setUserName() {
			if (this.#model.username) {
				let el = document.getElementById("username");
				if (!el) return;
				el.value = this.#model.username;
				el.focus();
			}
		};

		#handleBounce() {
			let debounce = (func, wait, immediate) => {
				let timeout;
				return function () {
					let context = this, args = arguments;
					let later = function () {
						timeout = null;
						if (!immediate) func.apply(context, args);
					};
					let callNow = immediate && !timeout;
					clearTimeout(timeout);
					timeout = setTimeout(later, wait);
					if (callNow) func.apply(context, args);
				};
			};

			let resizeHandler = debounce(() => {
				let newTopPadding;
				let winWidth = window.innerWidth;

				if (winWidth < 992) {
					newTopPadding = 70;
				} else {
					newTopPadding = Math.max(window.innerHeight / 4, 70);
					if (this.#model.externalProviders) {
						newTopPadding -= this.#model.externalProviders.length * 10;
					}
				}

				let el = document.getElementById("center-parent");
				if (el) {
					el.style.paddingTop = Math.max(newTopPadding) + 'px';
				}
			}, 250);

			window.addEventListener("resize", resizeHandler);
			window.dispatchEvent(new Event('resize'));
		}

		#setAdditionalLinks() {
			this.#setUlListItems(this.#model.additionalLinks, "additional-links", "list-unstyled");
		}

		#setExternalProviders() {
			this.#setUlListItems(this.#model.externalProviders, "external-providers", "list-unstyled");
		}

		#setFormVisibility() {
			this.#setElVisibility(this.#model.loginUrl, "shadow-box");
		}

		#setClosingHrVisibility() {
			this.#setElVisibility(!this.#model.externalProviders, "closing-hr");
		}

		#setExternalProvidersVisibility() {
			let visible = this.#model.externalProviders.length > 0;
			this.#setElVisibility(visible, "external-providers");
		}

		//----- worker methods

		#getElByClass(className) {
			return document.getElementsByClassName(className)[0];
		}

		#setUlListItems(list, parentClassName, ulClassName) {
			if (list && list.length > 0) {
				let ul = document.getElementsByClassName(parentClassName)[0].getElementsByClassName(ulClassName)[0];
				if (!ul) return;
				for (var i = 0; i < list.length; i++) {
					var li = document.createElement("li");

					var a = document.createElement("a");
					a.className = "btn btn-link";
					a.href = list[i].href;
					a.innerHTML = list[i].text;

					li.appendChild(a);
					ul.appendChild(li);
				}
			}
		}

		#setElVisibility(visible, className) {
			let el = this.#getElByClass(className);
			if (!el) return;
			if (visible) {
				el.style.display = "block";
			} else {
				el.style.display = "none";
			}
		}

		#getCookie(name) {
			function escape(s) { return s.replace(/([.*+?\^$(){}|\[\]\/\\])/g, '\\$1'); }
			var match = document.cookie.match(RegExp('(?:^|;\\s*)' + escape(name) + '=([^;]*)'));
			return match ? match[1] : null;
		}
	}

	let layout = new LayoutHydration();

})();
