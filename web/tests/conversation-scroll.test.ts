import { describe, expect, it, vi } from "vitest";
import { useConversationScroll } from "../src/conversation/useConversationScroll";

function setMetrics(
  element: HTMLElement,
  { scrollHeight, clientHeight, scrollTop }: Record<string, number>,
): void {
  Object.defineProperties(element, {
    scrollHeight: { configurable: true, value: scrollHeight },
    clientHeight: { configurable: true, value: clientHeight },
    scrollTop: { configurable: true, writable: true, value: scrollTop },
  });
}

describe("useConversationScroll", () => {
  it("starts FOLLOWING and detaches only when the user leaves the near-bottom threshold", () => {
    const scroll = useConversationScroll();
    const element = document.createElement("div");
    scroll.scroller.value = element;

    expect(scroll.state.value).toBe("FOLLOWING");
    expect(scroll.isDetached.value).toBe(false);

    setMetrics(element, { scrollHeight: 1000, clientHeight: 400, scrollTop: 503 });
    scroll.onScroll();
    expect(scroll.state.value).toBe("DETACHED");

    setMetrics(element, { scrollHeight: 1000, clientHeight: 400, scrollTop: 504 });
    scroll.onScroll();
    expect(scroll.state.value).toBe("FOLLOWING");
  });

  it("keeps a detached reader in place and restores following with a smooth jump", async () => {
    const scroll = useConversationScroll();
    const element = document.createElement("div");
    const scrollTo = vi.fn();
    Object.defineProperty(element, "scrollTo", { configurable: true, value: scrollTo });
    scroll.scroller.value = element;

    setMetrics(element, { scrollHeight: 1200, clientHeight: 400, scrollTop: 0 });
    scroll.onScroll();
    await scroll.followAfterUpdate();
    expect(element.scrollTop).toBe(0);

    scroll.followNow(true);
    expect(scroll.state.value).toBe("FOLLOWING");
    expect(scrollTo).toHaveBeenCalledWith({ top: 1200, behavior: "smooth" });
  });
});
