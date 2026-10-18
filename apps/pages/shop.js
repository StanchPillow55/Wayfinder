// Local shop mirror. No payment exists here. The cart lives in this browser tab's localStorage.
const WF_PRODUCTS = {
  planner: { name: "Large-print weekly planner", price: 14, blurb: "52 pages, 18-point type, lies flat." },
  labels: { name: "Tactile dot labels", price: 11, blurb: "Pack of 120 raised-dot stickers in six colors." },
  scale: { name: "Talking kitchen scale", price: 42, blurb: "Speaks the weight aloud. Battery included." },
};
const wfCart = () => JSON.parse(localStorage.getItem("wf-cart") || "[]").filter((id) => WF_PRODUCTS[id]);
const wfSave = (cart) => localStorage.setItem("wf-cart", JSON.stringify(cart));
const wfTotal = (cart) => cart.reduce((sum, id) => sum + WF_PRODUCTS[id].price, 0);
const wfCartLine = () => {
  const cart = wfCart();
  return `Cart: ${cart.length} item${cart.length === 1 ? "" : "s"}, $${wfTotal(cart)}.`;
};
document.querySelectorAll("[data-add]").forEach((button) => {
  button.addEventListener("click", () => {
    const id = button.dataset.add;
    const cart = wfCart();
    cart.push(id);
    wfSave(cart);
    const status = document.querySelector("#status");
    if (status) status.textContent = `Added ${WF_PRODUCTS[id].name} to cart. ${wfCartLine()}`;
  });
});
