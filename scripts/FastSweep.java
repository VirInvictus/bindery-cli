import java.io.File;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.Scanner;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import com.adobe.epubcheck.api.EpubCheck;
import com.adobe.epubcheck.reporting.CheckingReport;

/**
 * Parallel JVM epubcheck sweep. Reads one EPUB path per line from stdin and
 * validates all of them on a parallel stream, paying JVM startup once instead
 * of per book (the Python subprocess loop costs ~3 s of startup per book; this
 * turns a 7,000-book dry-run sweep from hours into minutes).
 *
 * Modes:
 *   --mode=audit    CSV "fatals,errors,warnings,path" for every book, clean
 *                   ones included: the format `bindery library --audit` reads.
 *   --mode=extract  "path ||| CODE,CODE" for every book with findings: the
 *                   error-code profile the candidate REPORT aggregates.
 *
 * Classpath needs epubcheck.jar and its lib/ dependencies:
 *   javac --release 25 -cp "epubcheck.jar:lib/*" FastSweep.java
 *   java  -cp ".:epubcheck.jar:lib/*" FastSweep --mode=extract < paths.txt
 *
 * scripts/fast_sweep.py does the compile-and-run plumbing; this file is the
 * whole harness.
 */
public class FastSweep {
    private static final Pattern CODE = Pattern.compile("([A-Z]{3}-\\d{3})");

    public static void main(String[] args) throws Exception {
        String mode = "audit";
        for (String a : args) {
            if (a.startsWith("--mode=")) mode = a.substring("--mode=".length()).trim();
        }
        if (!mode.equals("audit") && !mode.equals("extract")) {
            System.err.println("unknown mode: " + mode + " (use --mode=audit or --mode=extract)");
            System.exit(2);
        }
        final boolean extract = mode.equals("extract");

        List<String> paths = new ArrayList<>();
        try (Scanner scanner = new Scanner(System.in)) {
            while (scanner.hasNextLine()) {
                String path = scanner.nextLine().trim();
                if (!path.isEmpty()) paths.add(path);
            }
        }
        if (!extract) {
            System.out.println("fatals,errors,warnings,path");
        }

        paths.parallelStream().forEach(path -> {
            File epub = new File(path);
            if (!epub.exists()) return;
            try {
                StringWriter sw = new StringWriter();
                PrintWriter out = new PrintWriter(sw);
                CheckingReport report = new CheckingReport(out, epub.getName());
                // The CLI's own lifecycle (EpubChecker): initialize before
                // validation, generate after. Without initialize, generate()
                // NPEs on the unset start date; without generate, the report
                // buffers its messages and the writer stays empty — the failure
                // the original prototype shipped with (every code list came
                // back empty).
                report.initialize();
                EpubCheck check = new EpubCheck(epub, report);
                check.doValidate();
                if (extract) {
                    report.generate();
                    out.flush();
                    if (report.getErrorCount() == 0 && report.getFatalErrorCount() == 0
                            && report.getWarningCount() == 0) {
                        return;
                    }
                    List<String> codes = new ArrayList<>();
                    Matcher m = CODE.matcher(sw.toString());
                    while (m.find()) {
                        codes.add(m.group(1));
                    }
                    synchronized (System.out) {
                        System.out.println(epub.getCanonicalPath() + " ||| "
                                + String.join(",", codes));
                    }
                } else {
                    String res = report.getFatalErrorCount() + "," + report.getErrorCount()
                            + "," + report.getWarningCount() + "," + epub.getCanonicalPath();
                    synchronized (System.out) {
                        System.out.println(res);
                    }
                }
            } catch (Exception e) {
                // Same contract as the prototypes: a book the JVM cannot read is
                // absent from the output. The audit CSV keeps it a candidate on the
                // bindery side; extract counts are per book with findings.
            }
        });
    }
}
