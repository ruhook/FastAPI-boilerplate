from __future__ import annotations

import argparse
import json

from .shared import TMP_DIR, print_detail, print_step, run_module, timestamp_tag


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the consolidated V2 HR regression suite.")
    parser.add_argument("--skip-seed", action="store_true", help="Reuse the latest V2 seed summary.")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright browser E2E checks.")
    parser.add_argument(
        "--skip-advanced-filter-bulk",
        action="store_true",
        help="Skip the heavier advanced-filter bulk data regression.",
    )
    parser.add_argument(
        "--include-real-register-send",
        action="store_true",
        help="Allow the register verification suite to call real SMTP send-code.",
    )
    parser.add_argument("--register-send-email", default="", help="Email used with --include-real-register-send.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    print_step("Full V2 regression suite")
    runs: list[dict[str, object]] = []

    def run(name: str, module: str, *module_args: str) -> None:
        print_detail(f"running {name}")
        result = run_module(module, *module_args, log_prefix=f"v2-full-{name}")
        runs.append(
            {
                "name": name,
                "module": module,
                "args": list(module_args),
                "returncode": result.returncode,
                "log_path": result.log_path,
            }
        )

    if not args.skip_seed:
        run("seed", "src.scripts.v2.seed_manual_review_data")

    api_args = ["--skip-seed"]
    if not args.skip_advanced_filter_bulk:
        api_args.append("--include-advanced-filter-bulk")
    run("api-regression", "src.scripts.v2.run_api_regression_suite", *api_args)
    run("batch-contract", "src.scripts.v2.run_batch_contract_mutation_suite")
    run("permission-matrix", "src.scripts.v2.run_permission_matrix_suite")

    register_args: list[str] = []
    if args.include_real_register_send:
        register_args.append("--include-real-send")
        if args.register_send_email:
            register_args.extend(["--real-send-email", args.register_send_email])
    run("register-verification", "src.scripts.v2.run_register_verification_suite", *register_args)
    run("export-download", "src.scripts.v2.run_export_download_suite")
    if not args.skip_browser:
        run("browser-e2e", "src.scripts.v2.run_browser_e2e_suite")

    report = {
        "generated_at": timestamp_tag(),
        "skip_seed": bool(args.skip_seed),
        "skip_browser": bool(args.skip_browser),
        "skip_advanced_filter_bulk": bool(args.skip_advanced_filter_bulk),
        "runs": runs,
    }
    report_path = TMP_DIR / f"full-regression-v2-{timestamp_tag()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_detail(f"[PASS] full_regression: {len(runs)} modules")
    print_detail(f"report={report_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
