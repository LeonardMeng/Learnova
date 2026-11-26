import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ThemeToggle } from "./ThemeToggle";

describe("Theme button", () => {
  it("cycles System → Light → Dark and remembers the choice in this browser", async () => {
    const user = userEvent.setup();
    const view = render(<ThemeToggle />);
    const button = () => screen.getByRole("button", { name: /^Theme: / });

    expect(button()).toHaveAccessibleName("Theme: System");
    expect(document.documentElement.dataset.theme).toBe("light"); // jsdom has no matchMedia

    await user.click(button());
    expect(button()).toHaveAccessibleName("Theme: Light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("learnova.theme")).toBe("light");

    await user.click(button());
    expect(button()).toHaveAccessibleName("Theme: Dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("learnova.theme")).toBe("dark");

    view.unmount();
    render(<ThemeToggle />);
    expect(button()).toHaveAccessibleName("Theme: Dark");
    expect(document.documentElement.dataset.theme).toBe("dark");

    await user.click(button()); // back to System
    expect(button()).toHaveAccessibleName("Theme: System");
    expect(localStorage.getItem("learnova.theme")).toBe("system");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
