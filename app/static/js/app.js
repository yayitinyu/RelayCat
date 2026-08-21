document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;

    const prompt = form.dataset.confirm;
    if (prompt && !window.confirm(prompt)) {
        event.preventDefault();
        return;
    }

    const submitter = event.submitter;
    if (submitter instanceof HTMLButtonElement) {
        window.setTimeout(() => {
            submitter.disabled = true;
            submitter.setAttribute("aria-busy", "true");
        }, 0);
    }
});
