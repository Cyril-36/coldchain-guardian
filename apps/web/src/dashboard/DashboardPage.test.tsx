import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardPage } from "./DashboardPage";
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

