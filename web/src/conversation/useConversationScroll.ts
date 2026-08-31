import { nextTick, ref, type Ref } from "vue";

const NEAR_BOTTOM_PX = 96;

export type ConversationScrollState = "FOLLOWING" | "DETACHED";

function isNearBottom(element: HTMLElement): boolean {
  return element.scrollHeight - element.clientHeight - element.scrollTop <= NEAR_BOTTOM_PX;
}

export function useConversationScroll(): {
  scroller: Ref<HTMLElement | null>;
  state: Ref<ConversationScrollState>;
  isDetached: Ref<boolean>;
  onScroll: () => void;
  followAfterUpdate: () => Promise<void>;
  followNow: (smooth?: boolean) => void;
} {
  const scroller = ref<HTMLElement | null>(null);
  const state = ref<ConversationScrollState>("FOLLOWING");
  const isDetached = ref(false);

  function setState(next: ConversationScrollState): void {
    state.value = next;
    isDetached.value = next === "DETACHED";
  }

  function followNow(smooth = false): void {
    const element = scroller.value;
    if (!element) return;
    setState("FOLLOWING");
    if (smooth && typeof element.scrollTo === "function") {
      element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
    } else {
      element.scrollTop = element.scrollHeight;
    }
  }

  function onScroll(): void {
    const element = scroller.value;
    if (!element) return;
    setState(isNearBottom(element) ? "FOLLOWING" : "DETACHED");
  }

  async function followAfterUpdate(): Promise<void> {
    await nextTick();
    if (state.value === "FOLLOWING") followNow();
  }

  return { scroller, state, isDetached, onScroll, followAfterUpdate, followNow };
}
