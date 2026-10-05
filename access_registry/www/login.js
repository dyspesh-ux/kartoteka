/* Password sign-in of the registry: only before SSO is set up, or the administrator's emergency
   entry (/login?local=1). Two-factor codes are supported. */
(() => {
	"use strict";
	const form = document.querySelector("form.local");
	if (!form) return;
	const error = form.querySelector(".error");
	const otpBox = form.querySelector(".otp");
	let tmpId = null;

	const fail = (text) => {
		error.textContent = text;
		error.hidden = false;
	};

	async function call(args) {
		const response = await fetch("/api/method/login", {
			method: "POST",
			headers: { "Content-Type": "application/json", Accept: "application/json" },
			credentials: "same-origin",
			body: JSON.stringify(args),
		});
		let data = {};
		try {
			data = await response.json();
		} catch (e) {
			/* not JSON */
		}
		if (response.status === 401 || response.status === 403) throw new Error(data.message === "Login with username and password is not allowed." ? "Вход по паролю отключён" : "Неверный логин или пароль");
		if (response.status === 429) throw new Error("Слишком много попыток. Попробуйте позже");
		if (!response.ok) throw new Error("Не удалось войти. Попробуйте ещё раз");
		return data;
	}

	form.addEventListener("submit", async (e) => {
		e.preventDefault();
		error.hidden = true;
		const button = form.querySelector("button");
		button.disabled = true;
		try {
			const args = tmpId
				? { cmd: "login", otp: form.otp.value.trim(), tmp_id: tmpId }
				: { cmd: "login", usr: form.usr.value.trim(), pwd: form.pwd.value };
			const data = await call(args);
			if (data.verification && data.message !== "Logged In") {
				// second step: a code from the authenticator app, SMS or e-mail
				tmpId = data.tmp_id;
				document.cookie = "tmp_id=" + data.tmp_id;
				otpBox.hidden = false;
				form.otp.required = true;
				form.otp.focus();
				const v = data.verification;
				otpBox.querySelector(".otp-prompt").textContent =
					v.prompt || (v.setup ? "Введите код из приложения-аутентификатора" : "Отсканируйте QR-код приложением-аутентификатором и введите код");
				const qr = otpBox.querySelector(".otp-qr");
				if (v.qrcode && !v.setup) {
					qr.src = v.qrcode;
					qr.hidden = false;
				}
				return;
			}
			const target = form.dataset.redirect || data.home_page || "/registry";
			window.location.href = target;
		} catch (err) {
			fail(err.message);
		} finally {
			button.disabled = false;
		}
	});
})();
