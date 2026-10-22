//! CPU colour conversion of captured XRGB8888 (memory B,G,R,X) into the packed I420 layout the
//! encoder takes: Y rows (stride), then H/2 rows of [U (stride/2) | V (stride/2)]. BT.709, full or
//! limited range, 2x2 box-filtered chroma. Row bands run on scoped threads.
#![forbid(unsafe_code)]

use crate::gpu::Matrix;

#[derive(Clone, Copy)]
struct Coef {
    yr: i32,
    yg: i32,
    yb: i32,
    ur: i32,
    ug: i32,
    ub: i32,
    vr: i32,
    vg: i32,
    vb: i32,
    y_off: i32,
    y_scale: i32, // 16.16
    c_scale: i32, // 16.16
}

fn coef(m: Matrix) -> Coef {
    let (kr, kb, lim) = match m {
        Matrix::Bt709Full => (0.2126f64, 0.0722f64, false),
        Matrix::Bt709Limited => (0.2126, 0.0722, true),
        Matrix::Bt601Limited => (0.299, 0.114, true),
    };
    let kg = 1.0 - kr - kb;
    let f = |v: f64| (v * 65536.0).round() as i32;
    // U = (B - Y) / (2 (1 - kb)), V = (R - Y) / (2 (1 - kr))
    let du = 2.0 * (1.0 - kb);
    let dv = 2.0 * (1.0 - kr);
    Coef {
        yr: f(kr),
        yg: f(kg),
        yb: f(kb),
        ur: f(-kr / du),
        ug: f(-kg / du),
        ub: f((1.0 - kb) / du),
        vr: f((1.0 - kr) / dv),
        vg: f(-kg / dv),
        vb: f(-kb / dv),
        y_off: if lim { 16 } else { 0 },
        y_scale: if lim { f(219.0 / 255.0) } else { 65536 },
        c_scale: if lim { f(224.0 / 255.0) } else { 65536 },
    }
}

#[inline(always)]
fn clamp8(v: i32) -> u8 {
    v.clamp(0, 255) as u8
}

/// Convert rows [y0, y1) (even y0) of `src` into `y_out` (rows y0..y1) and `uv_out` (chroma rows
/// y0/2..y1/2, each [U | V]).
#[allow(clippy::too_many_arguments)]
fn band(src: &[u8], src_stride: usize, w: usize, y0: usize, y1: usize, y_out: &mut [u8], uv_out: &mut [u8], stride: usize, c: Coef) {
    let half = stride / 2;
    let cw = w / 2;
    for y in (y0..y1).step_by(2) {
        let r0 = &src[y * src_stride..y * src_stride + w * 4];
        let r1 = &src[(y + 1) * src_stride..(y + 1) * src_stride + w * 4];
        let (ya, yb) = y_out[(y - y0) * stride..(y - y0 + 2) * stride].split_at_mut(stride);
        let crow = &mut uv_out[((y - y0) / 2) * stride..((y - y0) / 2 + 1) * stride];
        let (u_row, v_row) = crow.split_at_mut(half);
        for x in 0..w {
            let p = &r0[x * 4..x * 4 + 3];
            let q = &r1[x * 4..x * 4 + 3];
            let ly0 = (c.yr * i32::from(p[2]) + c.yg * i32::from(p[1]) + c.yb * i32::from(p[0]) + 32768) >> 16;
            let ly1 = (c.yr * i32::from(q[2]) + c.yg * i32::from(q[1]) + c.yb * i32::from(q[0]) + 32768) >> 16;
            ya[x] = clamp8(c.y_off + ((ly0 * c.y_scale + 32768) >> 16));
            yb[x] = clamp8(c.y_off + ((ly1 * c.y_scale + 32768) >> 16));
        }
        for cx in 0..cw {
            let i = cx * 8;
            let (b, g, r) = (
                i32::from(r0[i]) + i32::from(r0[i + 4]) + i32::from(r1[i]) + i32::from(r1[i + 4]),
                i32::from(r0[i + 1]) + i32::from(r0[i + 5]) + i32::from(r1[i + 1]) + i32::from(r1[i + 5]),
                i32::from(r0[i + 2]) + i32::from(r0[i + 6]) + i32::from(r1[i + 2]) + i32::from(r1[i + 6]),
            );
            // sums of 4 -> divide by 4 inside the fixed point
            let u = (c.ur * r + c.ug * g + c.ub * b + 131072) >> 18;
            let v = (c.vr * r + c.vg * g + c.vb * b + 131072) >> 18;
            u_row[cx] = clamp8(128 + ((u * c.c_scale + 32768) >> 16));
            v_row[cx] = clamp8(128 + ((v * c.c_scale + 32768) >> 16));
        }
    }
}

/// XRGB8888 (`src_stride` bytes per row) -> packed I420 with row stride `stride` (>= w, even).
/// `h` and `w` must be even. `out` is resized to stride*h*3/2.
pub fn xrgb_to_i420(src: &[u8], src_stride: usize, w: usize, h: usize, stride: usize, m: Matrix, threads: usize, out: &mut Vec<u8>) {
    assert!(w % 2 == 0 && h % 2 == 0 && stride >= w);
    out.resize(stride * h * 3 / 2, 0);
    let c = coef(m);
    let (yp, uvp) = out.split_at_mut(stride * h);
    let threads = threads.clamp(1, 16);
    // bands of an even number of rows
    let rows_per = (h / threads).div_ceil(2) * 2;
    let rows_per = rows_per.max(2);
    std::thread::scope(|s| {
        let mut y_rest = yp;
        let mut uv_rest = uvp;
        let mut y0 = 0;
        while y0 < h {
            let y1 = (y0 + rows_per).min(h);
            let (ychunk, yr) = y_rest.split_at_mut((y1 - y0) * stride);
            let (uvchunk, uvr) = uv_rest.split_at_mut((y1 - y0) / 2 * stride);
            y_rest = yr;
            uv_rest = uvr;
            if y1 == h {
                band(src, src_stride, w, y0, y1, ychunk, uvchunk, stride, c);
            } else {
                s.spawn(move || band(src, src_stride, w, y0, y1, ychunk, uvchunk, stride, c));
            }
            y0 = y1;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reference(r: f64, g: f64, b: f64, m: Matrix) -> (f64, f64, f64) {
        let (kr, kb, lim) = match m {
            Matrix::Bt709Full => (0.2126, 0.0722, false),
            Matrix::Bt709Limited => (0.2126, 0.0722, true),
            Matrix::Bt601Limited => (0.299, 0.114, true),
        };
        let y = kr * r + (1.0 - kr - kb) * g + kb * b;
        let u = (b - y) / (2.0 * (1.0 - kb));
        let v = (r - y) / (2.0 * (1.0 - kr));
        if lim { (16.0 + y * 219.0 / 255.0, 128.0 + u * 224.0 / 255.0, 128.0 + v * 224.0 / 255.0) } else { (y, 128.0 + u, 128.0 + v) }
    }

    #[test]
    fn matches_the_reference_within_one_code_value() {
        let (w, h) = (6usize, 4usize);
        let cols = [(255u8, 0u8, 0u8), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0), (120, 200, 40)];
        let mut src = vec![0u8; w * 4 * h];
        for y in 0..h {
            for x in 0..w {
                let (r, g, b) = cols[x];
                src[(y * w + x) * 4..(y * w + x) * 4 + 4].copy_from_slice(&[b, g, r, 0xff]);
            }
        }
        for m in [Matrix::Bt709Full, Matrix::Bt709Limited, Matrix::Bt601Limited] {
            let mut out = Vec::new();
            xrgb_to_i420(&src, w * 4, w, h, 16, m, 2, &mut out);
            for x in 0..w {
                let (r, g, b) = cols[x];
                let (ry, _, _) = reference(f64::from(r), f64::from(g), f64::from(b), m);
                assert!((f64::from(out[x]) - ry).abs() <= 1.0, "{m:?} Y at {x}: {} vs {ry}", out[x]);
            }
            // chroma of the 2x2 block of identical columns 0,1 -> average of red and green
            let (r, g, b) = (127.5, 127.5, 0.0);
            let (_, ru, rv) = reference(r, g, b, m);
            let u = f64::from(out[16 * h]);
            let v = f64::from(out[16 * h + 8]);
            assert!((u - ru).abs() <= 1.0 && (v - rv).abs() <= 1.0, "{m:?} UV {u},{v} vs {ru},{rv}");
        }
    }

    #[test]
    fn threads_do_not_change_the_result() {
        let (w, h) = (64usize, 38usize);
        let src: Vec<u8> = (0..w * h * 4).map(|i| (i * 7 % 251) as u8).collect();
        let mut a = Vec::new();
        let mut b = Vec::new();
        xrgb_to_i420(&src, w * 4, w, h, 64, Matrix::Bt709Full, 1, &mut a);
        xrgb_to_i420(&src, w * 4, w, h, 64, Matrix::Bt709Full, 5, &mut b);
        assert_eq!(a, b);
    }
}
