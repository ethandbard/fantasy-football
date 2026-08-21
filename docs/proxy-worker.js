const ORIGIN = "https://ethandbard.github.io/fantasy-football";

export default {
  async fetch(request) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405 });
    }

    const inbound = new URL(request.url);
    const target = new URL(ORIGIN + inbound.pathname + inbound.search);
    const response = await fetch(target, {
      method: request.method,
      redirect: "follow",
    });

    return new Response(response.body, {
      status: response.status,
      headers: response.headers,
    });
  },
};
