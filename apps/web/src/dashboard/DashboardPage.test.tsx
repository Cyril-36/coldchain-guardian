import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardPage, formatDuration } from "./DashboardPage";
import { AuthProvider } from "../auth/AuthProvider";

function renderWithAuth(ui: React.ReactElement) {
  return render(<AuthProvider>{ui}</AuthProvider>);
}

describe("DashboardPage presentation and interactions", () => {
  it("renders the dashboard shell with simulated banner and metric cards", () => {
    renderWithAuth(<DashboardPage />);
    expect(screen.getAllByText("ColdChain Guardian")[0]).toBeInTheDocument();
    expect(screen.getByText(/Demo fixture mode/)).toBeInTheDocument();

    const summary = screen.getByRole("region", { name: "Investigation summary" });
    expect(summary).toHaveTextContent("Observed peak");
    expect(summary).toHaveTextContent("Estimated time out of range");
    expect(summary).toHaveTextContent("Data coverage");
    expect(summary).toHaveTextContent("Review status");
  });

  it("handles evidence selection and toggle", () => {
    renderWithAuth(<DashboardPage />);
    const evidenceButtons = screen.getAllByRole("button", { pressed: false });
    const firstEvidence = evidenceButtons.find((btn) => btn.textContent?.includes("Door event") || btn.textContent?.includes("Door opened"));
    expect(firstEvidence).toBeDefined();

    if (firstEvidence) {
      fireEvent.click(firstEvidence);
      expect(firstEvidence).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByText(/Evidence selection highlighted on chart/)).toBeInTheDocument();

      fireEvent.click(firstEvidence);
      expect(firstEvidence).toHaveAttribute("aria-pressed", "false");
    }
  });

  it("renders the operator review form and notes regulatory disclaimer", () => {
    renderWithAuth(<DashboardPage />);
    const headings = screen.getAllByRole("heading", { name: "Operator review" });
    expect(headings.length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Review acknowledgement records operator review only and does not constitute shipment release/)[0]).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Acknowledge review" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request evidence" })).toBeInTheDocument();
  });

  it("allows typing review note and submitting fixture review", async () => {
    renderWithAuth(<DashboardPage />);
    const textarea = screen.getByPlaceholderText("Add review note…");
    fireEvent.change(textarea, { target: { value: "Checked door sensor logs and confirmed excursion." } });

    const ackButton = screen.getByRole("button", { name: "Acknowledge review" });
    expect(ackButton).not.toBeDisabled();

    fireEvent.click(ackButton);
    expect(await screen.findByText("Review saved.")).toBeInTheDocument();
    expect(await screen.findByTestId("reviewer-identity")).toHaveTextContent("operator-session-fixture");
  });
});

describe("formatDuration unit behavior", () => {
  it("formats whole, sub-minute, and minute boundaries cleanly", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatDuration(0)).toBe("0s");
    expect(formatDuration(30)).toBe("30s");
    expect(formatDuration(60)).toBe("1 min");
    expect(formatDuration(90)).toBe("1 min 30s");
  });

  it("preserves one-decimal backend durations without rounding 59.9s to 60s or 119.9s to 1 min 60s", () => {
    expect(formatDuration(59.9)).toBe("59.9s");
    expect(formatDuration(119.9)).toBe("1 min 59.9s");
    expect(formatDuration(60.5)).toBe("1 min 0.5s");
    expect(formatDuration(59.99)).toBe("1 min");
    expect(formatDuration(119.99)).toBe("2 min");
  });
});

